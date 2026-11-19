import contextlib
import html
import queue
import shutil
import subprocess as sp
import threading
import time
from collections import deque
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # Защита от ошибок GUI при сохранении графиков в Streamlit

import streamlit as st
import test as wk  # <-- Имя вашего исходного .py файла (без .py)
from inp_redactor import inp_redactor

st.set_page_config(
    page_title="Object Worker — Генерация и расчёт",
    layout="wide",
)


def fmt_time(seconds: float) -> str:
    """Форматирует секунды в вид ЧЧ:ММ:СС или ММ:СС."""
    seconds = max(0.0, seconds)
    mins, secs = divmod(int(seconds), 60)
    hours, mins = divmod(mins, 60)
    if hours > 0:
        return f"{hours:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


def format_terminal_html(text: str, height: int = 400) -> str:
    """Оборачивает текст в фиксированное окно терминала с автопрокруткой вниз."""
    escaped = html.escape(text).replace("\n", "<br>")
    return (
        f'<div style="height: {height}px; overflow-y: auto; display: flex; '
        f'flex-direction: column-reverse; background-color: #0e1117; color: #e6edf3; '
        f'padding: 12px 14px; border-radius: 8px; border: 1px solid #30363d; '
        f"font-family: 'Consolas', 'Courier New', monospace; font-size: 13px; "
        f'line-height: 1.45;">'
        f'<div style="white-space: pre-wrap; word-break: break-word;">{escaped}</div>'
        f'</div>'
    )


class LiveConsole:
    """Буфер для вывода командной строки и print() в реальном времени."""

    def __init__(self, placeholder, on_tick=None, max_lines: int = 500, height: int = 400):
        self.placeholder = placeholder
        self.on_tick = on_tick
        self.height = height
        self.lines = deque(maxlen=max_lines)
        self.full_log = []
        self._last_render = 0.0

    def add_line(self, text: str, force: bool = False):
        line = text.rstrip("\r\n")
        if not line:
            return
        self.lines.append(line)
        self.full_log.append(line)
        self.render(force=force)

    def write(self, data: str):
        if not data:
            return
        for part in data.splitlines():
            if part.strip():
                self.lines.append(part)
                self.full_log.append(part)
        self.render()

    def flush(self):
        self.render(force=True)

    def render(self, force: bool = False):
        now = time.perf_counter()
        if force or (now - self._last_render >= 0.1):
            content = "\n".join(self.lines) if self.lines else "Ожидание вывода..."
            self.placeholder.markdown(
                format_terminal_html(content, height=self.height),
                unsafe_allow_html=True,
            )
            if self.on_tick:
                self.on_tick()
            self._last_render = now


class QueueWriter:
    """Потокобезопасный перехватчик print() для фоновых потоков."""

    def __init__(self, out_queue: queue.Queue):
        self.out_queue = out_queue

    def write(self, data: str):
        if data:
            for part in data.splitlines():
                if part.strip():
                    self.out_queue.put(part)

    def flush(self):
        pass


def run_command_stream(cmd, console: LiveConsole, cwd=None, line_handler=None, on_tick=None):
    """Запускает процесс, читает stdout в фоновом потоке и регулярно обновляет таймер."""
    cmd_str = cmd if isinstance(cmd, str) else " ".join(map(str, cmd))
    console.add_line(f"$ {cmd_str}", force=True)

    proc = sp.Popen(
        cmd,
        cwd=cwd,
        shell=True,
        stdout=sp.PIPE,
        stderr=sp.STDOUT,
        text=True,
        errors="replace",
        bufsize=1,
    )

    line_queue: queue.Queue = queue.Queue()

    def _reader():
        if proc.stdout:
            for line in proc.stdout:
                line_queue.put(line)
        line_queue.put(None)

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()

    while True:
        try:
            line = line_queue.get(timeout=0.5)
        except queue.Empty:
            if on_tick:
                on_tick()
            if not reader_thread.is_alive() and line_queue.empty():
                break
            continue

        if line is None:
            break

        console.add_line(line)
        if line_handler:
            line_handler(line)

    retcode = proc.wait()
    console.flush()
    if on_tick:
        on_tick()
    return retcode


def run_func_with_timer(func, *args, console: LiveConsole, on_tick=None, **kwargs):
    """Запускает Python-функцию в фоновом потоке, чтобы таймер продолжал идти."""
    out_queue: queue.Queue = queue.Queue()
    result_box = {"error": None}

    def _worker():
        try:
            with contextlib.redirect_stdout(QueueWriter(out_queue)):
                func(*args, **kwargs)
        except Exception as exc:
            result_box["error"] = exc

    worker = threading.Thread(target=_worker, daemon=True)
    worker.start()

    while worker.is_alive() or not out_queue.empty():
        try:
            line = out_queue.get(timeout=0.5)
            console.add_line(line)
        except queue.Empty:
            if on_tick:
                on_tick()

    console.flush()
    if on_tick:
        on_tick()

    if result_box["error"] is not None:
        raise result_box["error"]


def make_sp_run_hook(console: LiveConsole, default_cwd: str, on_tick=None):
    """Обёртка над subprocess.run внутри test.py для перехвата вывода ntopcl."""
    def _hooked_run(cmd, *args, **kwargs):
        cwd = kwargs.get("cwd", default_cwd)
        retcode = run_command_stream(cmd, console, cwd=cwd, on_tick=on_tick)
        return sp.CompletedProcess(args=cmd, returncode=retcode)
    return _hooked_run


def render_queue_table(placeholder, status_rows: list[dict]):
    """Отрисовывает таблицу состояния очереди задач."""
    placeholder.dataframe(
        status_rows,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Задача": st.column_config.TextColumn("Задача / Объект", width="medium"),
            "Статус": st.column_config.TextColumn("Статус", width="small"),
            "Этап": st.column_config.TextColumn("Текущий этап", width="medium"),
            "Время": st.column_config.TextColumn("Время", width="small"),
        },
    )


# ==================== БОКОВАЯ ПАНЕЛЬ (НАСТРОЙКИ) ====================
st.sidebar.header("Настройки запуска")

work_dir_str = st.sidebar.text_input("Рабочая папка (WORK_DIR)", value=wk.WORK_DIR)
obj_folder_name = st.sidebar.text_input("Папка с .obj файлами", value="OBJECT_FOLDER")

work_dir_path = Path(work_dir_str)
obj_dir_path = work_dir_path / obj_folder_name

# Поиск CSV-файлов для генерации структур
csv_files = sorted([f.stem for f in work_dir_path.glob("*.csv")]) if work_dir_path.exists() else []

st.sidebar.divider()
st.sidebar.subheader("1. Генерация структур (.csv)")

gen_plan: dict[str, int] = {}
if csv_files:
    for csv_name in csv_files:
        cnt = st.sidebar.number_input(
            f"{csv_name}",
            min_value=0,
            max_value=100,
            value=0,
            step=1,
            key=f"csv_{csv_name}",
        )
        if cnt > 0:
            gen_plan[csv_name] = int(cnt)
else:
    st.sidebar.caption("В рабочей папке нет .csv файлов.")

total_to_generate = sum(gen_plan.values())

st.sidebar.divider()
st.sidebar.subheader("2. Методы исследования")

pore_num = st.sidebar.number_input("Пористость (PoreNum)", min_value=0, value=int(wk.PoreNum), step=1)
slice_num = st.sidebar.number_input("Локальная толщина (SliceNum)", min_value=0, value=int(wk.SliceNum), step=1)
mesh_flag = st.sidebar.checkbox("Анизотропия в Abaqus (MeshFlag)", value=bool(wk.MeshFlag))

if mesh_flag:
    col_c, col_m = st.sidebar.columns(2)
    abaqus_cpus = col_c.number_input("CPUs", min_value=1, max_value=32, value=2)
    abaqus_mem = col_m.text_input("Memory", value="7200mb")
else:
    abaqus_cpus, abaqus_mem = 2, "7200mb"

st.sidebar.divider()
refresh_btn = st.sidebar.button("Обновить списки", use_container_width=True)
start_btn = st.sidebar.button("Запустить обработку", type="primary", use_container_width=True)

# ==================== ОСНОВНОЙ ЭКРАН ====================
st.title("Object Worker — Генерация и исследование")

if not work_dir_path.exists():
    st.error(f"Рабочая папка не найдена: `{work_dir_path}`")
    st.stop()

obj_dir_path.mkdir(parents=True, exist_ok=True)
existing_obj_list = wk.find_obj_files(str(obj_dir_path))

# Формируем предварительную очередь
gen_tasks = []
for csv_name, count in gen_plan.items():
    for i in range(1, count + 1):
        gen_tasks.append((csv_name, i))

status_rows = [
    {"Задача": f"🧬 {csv_name} #{i}", "Статус": "⏳ В очереди", "Этап": "Генерация (BicontMaker)", "Время": "—"}
    for csv_name, i in gen_tasks
] + [
    {"Задача": f"📦 {obj}", "Статус": "⏳ В очереди", "Этап": "Исследование", "Время": "—"}
    for obj in existing_obj_list
]

# Верхние метрики
m1, m2, m3, m4 = st.columns(4)
metric_total_time = m1.empty()
metric_progress = m2.empty()
metric_current_task = m3.empty()
metric_task_time = m4.empty()

progress_bar = st.progress(0.0, text="Готов к запуску")

col_queue, col_term = st.columns([1, 1.3], gap="large")

with col_queue:
    queue_header = st.empty()
    queue_header.subheader(f"Очередь задач ({len(status_rows)})")
    queue_placeholder = st.empty()

with col_term:
    st.subheader("Командная строка (в реальном времени)")
    console_placeholder = st.empty()

metric_total_time.metric("Общее время", "00:00")
metric_progress.metric("Выполнено", f"0 / {len(status_rows)}")
metric_current_task.metric("Текущая задача", "—")
metric_task_time.metric("Время задачи", "00:00")

if status_rows:
    render_queue_table(queue_placeholder, status_rows)
else:
    queue_placeholder.info("Выберите .csv для генерации слева или поместите .obj файлы в OBJECT_FOLDER.")

console_placeholder.markdown(
    format_terminal_html("Нажмите «Запустить обработку» в меню слева."),
    unsafe_allow_html=True,
)

# ==================== ЗАПУСК ПАЙПЛАЙНА ====================
if start_btn:
    if not gen_tasks and not existing_obj_list:
        st.warning("Нет задач: выберите количество структур для генерации или добавьте .obj файлы.")
        st.stop()

    wk.WORK_DIR = str(work_dir_path)
    wk.OBJ_DIR = obj_dir_path
    wk.PoreNum = int(pore_num)
    wk.SliceNum = int(slice_num)
    wk.MeshFlag = bool(mesh_flag)

    start_total = time.perf_counter()
    current_task_name = "—"
    start_task_time = start_total
    done_count = 0
    total_tasks = len(gen_tasks) + len(existing_obj_list)

    def update_timers():
        now = time.perf_counter()
        metric_total_time.metric("Общее время", fmt_time(now - start_total))
        metric_progress.metric("Выполнено", f"{done_count} / {total_tasks}")
        metric_current_task.metric("Текущая задача", current_task_name)
        metric_task_time.metric("Время задачи", fmt_time(now - start_task_time))

    console = LiveConsole(console_placeholder, on_tick=update_timers)

    original_sp_run = wk.sp.run
    wk.sp.run = make_sp_run_hook(console, default_cwd=str(work_dir_path), on_tick=update_timers)

    try:
        # ---------- ЭТАП 1: ГЕНЕРАЦИЯ СТРУКТУР ----------
        if gen_tasks:
            console.add_line(f"=== Старт генерации структур ({len(gen_tasks)} шт.) ===", force=True)

            for idx, (csv_name, sample_idx) in enumerate(gen_tasks):
                current_task_name = f"{csv_name} #{sample_idx}"
                start_task_time = time.perf_counter()

                status_rows[idx]["Статус"] = "⚙️ В работе"
                status_rows[idx]["Этап"] = "nTop (BicontMaker.ntop)"
                render_queue_table(queue_placeholder, status_rows)
                progress_bar.progress(
                    done_count / max(total_tasks, 1),
                    text=f"Генерация '{csv_name}' № {sample_idx}...",
                )
                update_timers()

                try:
                    console.add_line(f"Создание биконта '{csv_name}' № {sample_idx}", force=True)
                    wk.make_bicont_from_point_map(csv_name)

                    elapsed = time.perf_counter() - start_task_time
                    done_count += 1
                    status_rows[idx]["Статус"] = "✅ Готово"
                    status_rows[idx]["Этап"] = "Сгенерировано"
                    status_rows[idx]["Время"] = f"{elapsed:.1f} с"
                    console.add_line(f"Биконт '{csv_name}' № {sample_idx} создан за {elapsed:.2f} с\n", force=True)
                except Exception as e:
                    elapsed = time.perf_counter() - start_task_time
                    status_rows[idx]["Статус"] = "❌ Ошибка"
                    status_rows[idx]["Этап"] = str(e)[:40]
                    status_rows[idx]["Время"] = f"{elapsed:.1f} с"
                    console.add_line(f"ОШИБКА генерации '{csv_name}' № {sample_idx}: {e}", force=True)

                render_queue_table(queue_placeholder, status_rows)
                update_timers()

        # ---------- ПЕРЕСКАНИРОВАНИЕ OBJECT_FOLDER ----------
        obj_list = wk.find_obj_files(str(obj_dir_path))
        should_investigate = bool(mesh_flag or pore_num != 0 or slice_num != 0)

        # Обновляем таблицу очереди: оставляем выполненные генерации + актуальный список .obj
        status_rows = status_rows[: len(gen_tasks)]
        if should_investigate:
            for obj in obj_list:
                status_rows.append(
                    {"Задача": f"📦 {obj}", "Статус": "⏳ В очереди", "Этап": "Ожидание", "Время": "—"}
                )
            total_tasks = len(gen_tasks) + len(obj_list)
        else:
            total_tasks = len(gen_tasks)

        queue_header.subheader(f"Очередь задач ({len(status_rows)})")
        render_queue_table(queue_placeholder, status_rows)
        update_timers()

        # ---------- ЭТАП 2: ИССЛЕДОВАНИЕ МОДЕЛЕЙ ----------
        if should_investigate and obj_list:
            console.add_line(f"=== Старт исследования моделей ({len(obj_list)} шт.) ===", force=True)

            for i_obj, obj in enumerate(obj_list):
                row_idx = len(gen_tasks) + i_obj
                current_task_name = obj
                start_task_time = time.perf_counter()
                obj_dir = obj_dir_path / obj
                obj_dir.mkdir(exist_ok=True)

                status_rows[row_idx]["Статус"] = "⚙️ В работе"
                status_rows[row_idx]["Этап"] = "Инициализация"
                render_queue_table(queue_placeholder, status_rows)
                update_timers()

                try:
                    # 1. Запуск nTop
                    status_rows[row_idx]["Этап"] = "nTop (Object_worker.ntop)"
                    render_queue_table(queue_placeholder, status_rows)
                    progress_bar.progress(
                        done_count / max(total_tasks, 1),
                        text=f"[{obj}] Запуск nTop...",
                    )
                    wk.obj_ntop_worker(obj, int(pore_num), int(slice_num), bool(mesh_flag))

                    # 2. График пористости
                    if pore_num != 0:
                        status_rows[row_idx]["Этап"] = "График пористости"
                        render_queue_table(queue_placeholder, status_rows)
                        console.add_line(f"[{obj}] Построение графика пористости...", force=True)
                        run_func_with_timer(wk.pore_graph, obj, obj_dir, console=console, on_tick=update_timers)

                    # 3. Локальная толщина
                    if slice_num != 0:
                        status_rows[row_idx]["Этап"] = "Локальная толщина"
                        render_queue_table(queue_placeholder, status_rows)
                        console.add_line(f"[{obj}] Расчёт локальной толщины...", force=True)
                        run_func_with_timer(wk.thinkness_graph, obj, obj_dir, console=console, on_tick=update_timers)

                    # 4. Анизотропия (Abaqus)
                    if mesh_flag:
                        for suffix in ["_X", "_Y", "_Z"]:
                            status_rows[row_idx]["Этап"] = f"Abaqus ({suffix})"
                            render_queue_table(queue_placeholder, status_rows)

                            out_name = f"{obj}_model{suffix}"
                            in_name = f"{obj}{suffix}"
                            sub_work_dir = str(obj_dir)

                            run_func_with_timer(
                                inp_redactor,
                                out_name,
                                in_name,
                                sub_work_dir,
                                console=console,
                                on_tick=update_timers,
                                delete_in_file=True,
                                print_done=True,
                            )

                            start_abaqus_time = time.perf_counter()
                            state = {"download_time": None, "happy_end": False}

                            def abaqus_line_handler(line: str):
                                if "Run standard.exe" in line:
                                    state["download_time"] = time.perf_counter() - start_abaqus_time
                                    console.add_line(
                                        f"Загрузка модели в Abaqus, время: {state['download_time']:.2f} с"
                                    )
                                elif "completed" in line.lower():
                                    dt = state["download_time"] or 0.0
                                    count_time = time.perf_counter() - dt - start_abaqus_time
                                    console.add_line(f"Расчёт модели, время: {count_time:.2f} с")
                                    state["happy_end"] = True

                            abaqus_cmd = [
                                "abaqus",
                                f"job=Job-{out_name}",
                                f"input={out_name}.inp",
                                f"cpus={abaqus_cpus}",
                                f"memory={abaqus_mem}",
                                "interactive",
                                "ask=off",
                            ]
                            run_command_stream(
                                abaqus_cmd,
                                console,
                                cwd=sub_work_dir,
                                line_handler=abaqus_line_handler,
                                on_tick=update_timers,
                            )

                            if not state["happy_end"]:
                                console.add_line(f"Ошибка при подсчёте {out_name} в Abaqus", force=True)

                            protected_exts = {".odb"}
                            files_to_delete = [
                                f for f in obj_dir.iterdir()
                                if f.is_file()
                                and f.name.startswith("Job-" + out_name)
                                and f.suffix.lower() not in protected_exts
                            ]
                            for f in files_to_delete:
                                try:
                                    f.unlink()
                                    console.add_line(f"Удалён: {f.name}")
                                except Exception as e:
                                    console.add_line(f"Ошибка при удалении {f.name}: {e}")

                            status_rows[row_idx]["Этап"] = f"Парсинг ODB ({suffix})"
                            render_queue_table(queue_placeholder, status_rows)
                            run_command_stream(
                                f'abaqus python abaqus_parsing.py "{obj_dir}"',
                                console,
                                cwd=str(work_dir_path),
                                on_tick=update_timers,
                            )

                            elapsed_abq = time.perf_counter() - start_abaqus_time
                            console.add_line(f"Расчёт {out_name} завершён за {elapsed_abq:.2f} с", force=True)

                    # Перемещение .obj файла в его папку
                    src_obj = obj_dir_path / f"{obj}.obj"
                    if src_obj.exists():
                        shutil.move(str(src_obj), str(obj_dir / f"{obj}.obj"))

                    obj_elapsed = time.perf_counter() - start_task_time
                    done_count += 1
                    status_rows[row_idx]["Статус"] = "✅ Готово"
                    status_rows[row_idx]["Этап"] = "Завершено"
                    status_rows[row_idx]["Время"] = f"{obj_elapsed:.1f} с"
                    console.add_line(f"Время обработки '{obj}': {obj_elapsed:.2f} с\n", force=True)

                except Exception as e:
                    obj_elapsed = time.perf_counter() - start_task_time
                    status_rows[row_idx]["Статус"] = "❌ Ошибка"
                    status_rows[row_idx]["Этап"] = str(e)[:40]
                    status_rows[row_idx]["Время"] = f"{obj_elapsed:.1f} с"
                    console.add_line(f"ОШИБКА при обработке '{obj}': {e}", force=True)

                render_queue_table(queue_placeholder, status_rows)
                progress_bar.progress(
                    done_count / max(total_tasks, 1),
                    text=f"Выполнено {done_count} из {total_tasks}",
                )
                update_timers()

    finally:
        wk.sp.run = original_sp_run

    total_elapsed = time.perf_counter() - start_total
    current_task_name = "Завершено"
    update_timers()
    progress_bar.progress(1.0, text=f"Все задачи завершены за {fmt_time(total_elapsed)}")
    st.success(f"Обработка завершена. Общее время: {total_elapsed:.2f} с")

    with st.expander("Скачать / посмотреть полный лог"):
        full_text = "\n".join(console.full_log)
        st.download_button("Скачать log.txt", data=full_text, file_name="worker_log.txt")
        st.code(full_text, language="bash")