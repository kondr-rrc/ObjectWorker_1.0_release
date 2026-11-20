from pathlib import Path
import zipfile
import subprocess as sp
import numpy as np
import matplotlib.pyplot as plt
import time
import shutil

from inp_redactor import inp_redactor
from thinkness import local_pore_and_inclusions_thickness

rng = np.random.default_rng()

def find_obj_files(folder: str) -> list[str]:
    """Возвращает список имён файлов с расширением .obj в указанной папке."""
    return [f.stem for f in Path(folder).glob("*.obj")]

def extract_zip_ti_own_folder(zip_path: Path) -> Path:
    """
    Распаковывает ZIP-архив в папку с тем же именем,
    расположенную рядом с архивом, после чего удаляет сам архив.

    Если папка назначения уже существует — её содержимое полностью удаляется
    перед распаковкой. После успешной распаковки сам архив удаляется.
    """
    zip_path = Path(zip_path)

    if not zip_path.is_file():
        raise FileNotFoundError(f"Файл не найден: {zip_path}")

    if zip_path.suffix.lower() != ".zip":
        raise ValueError(f"Ожидался .zip файл, получен: {zip_path.name}")

    out_dir = zip_path.with_suffix("")
    if out_dir.exists():
        for item in out_dir.iterdir():
            item.unlink()
    out_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(out_dir)

    zip_path.unlink()
    print(f"\nРаспакован: {zip_path.name} -> {out_dir}")

    return out_dir

def pore_txt_to_point_map(pore_txt):
    with open(f'{WORK_DIR}/{pore_txt}.txt', 'r') as f:
        data = f.read().strip().split(', ')
    data = np.array([float(i) for i in data])

    y_arr = np.linspace(0.0, 5.0, len(data))
    x_arr, z_arr = np.linspace(0.0, 5.0, 25), np.linspace(0.0, 5.0, 25)

    pore_interpolate = lambda arg: np.interp(arg, y_arr, data)

    point_map = []

    for i in range(len(data)):
        for j in range(25):
            for k in range(25):
                point_map.append(np.array([x_arr[j], y_arr[i], z_arr[k], pore_interpolate(y_arr[i])]))
    pore_map = np.array(point_map)
    np.savetxt(f'{WORK_DIR}/{pore_txt}.csv', pore_map, delimiter=",", fmt="%.5f")

def make_bicont_from_point_map(point_map_name):
    random_seed = rng.integers(1, 1e4)

    ntop_command = f"ntopcl -v2 -i {random_seed} -i {point_map_name} BicontMaker.ntop"
    sp.run(ntop_command, shell=True)

def obj_ntop_worker(obj, pore_num, slice_num, mesh_flag):
    if mesh_flag or pore_num != 0 or slice_num != 0:
        ntop_command = f"ntopcl -v2 -i {obj} -i {pore_num} -i {slice_num} -i {int(mesh_flag)} Object_worker.ntop"
        sp.run(ntop_command, shell=True)

def pore_graph(obj, obj_dir):
    pore_data = np.genfromtxt(f"{obj_dir}/{obj}_porosity.txt", delimiter=',', autostrip=True)

    y_arr = np.linspace(0.0, 5.0, len(pore_data))
    pore_interpolate = lambda arg: np.interp(arg, y_arr, pore_data)

    y_inter_arr = np.linspace(0.0, 5.0, 1000)
    p_inter_arr = np.array([pore_interpolate(x) for x in y_inter_arr])

    plt.scatter(y_arr, pore_data, s=1.9, c='r', zorder=2, label='Точки расчёта')
    plt.plot(y_inter_arr, p_inter_arr, zorder=1, label='Интерполяция')
    plt.title(f'График пористости {obj}')
    plt.savefig(f"{(OBJ_DIR / obj)}/{obj}_porosity_graph.png", dpi=300)
    plt.xlabel('Координата y')
    plt.ylabel('Пористость')
    plt.close()

def thinkness_graph(obj, obj_dir):
    slice_path = extract_zip_ti_own_folder(f"{obj_dir}/{obj}_slices.zip")
    thickness_data = np.zeros((SliceNum, 2))
    for slice in range(SliceNum):
        thickness_data[slice] = local_pore_and_inclusions_thickness((slice + 1), str(slice_path))

    #Первый слайс всегда косячный из-за оссобенностей слайсера и алгоритма создания сетки
    thickness_data[0] = thickness_data[1]

    l_pixel = 5 / 256  # Длина пикселя в мм
    thickness_data = l_pixel * thickness_data
    slice_coord = np.linspace(0.0, 5.0, SliceNum)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(8, 4))

    # ax1.scatter(slice_coord,thickness_data[:, 0], s=3, c='r')
    ax1.plot(slice_coord, thickness_data[:, 0])
    ax1.set_title('Средняя локальная толщина включений')

    # ax2.scatter(slice_coord,thickness_data[:, 1], s=3, c='r')
    ax2.plot(slice_coord, thickness_data[:, 1])
    ax2.set_title('Средняя локальная толщина пор')

    ax1.grid()
    ax2.grid()
    plt.tight_layout()
    plt.savefig(f"{obj_dir}/{obj}_slices.png", dpi=300)
    plt.close()

    np.savetxt(f"{obj_dir}/{obj}_slices.dat", thickness_data)

WORK_DIR = r"C:\Users\student\Desktop\ObjectWorker_1.0_release"
OBJ_DIR = Path(WORK_DIR) / "OBJECT_FOLDER"

PoreNum = 0
SliceNum = 0
MeshFlag = False

if __name__ == "__main__":
    start = time.perf_counter()

    obj_list = find_obj_files(OBJ_DIR)
    print(f'Количество моедлей: {len(obj_list)}')
    print(obj_list)
    for obj in obj_list:
        start_obj_time = time.perf_counter()

        obj_dir = OBJ_DIR / obj

        # Создаём папку для хранения данных под каждую модель
        obj_dir.mkdir(exist_ok=True)

        # Запускаем nTop файл для создания данных для графиков
        obj_ntop_worker(obj, PoreNum, SliceNum, MeshFlag)

        # Построение графика пористости
        if PoreNum != 0:
            pore_graph(obj, obj_dir)

        # Построение графиков локальной толщины и ширины пор
        if SliceNum != 0:
            thinkness_graph(obj, obj_dir)

        # Вычисление коэффициента Анизотропии
        if MeshFlag:
            for suffix in ['_X', '_Y', '_Z']:
                out_name = f"{obj}_model{suffix}"
                in_name = f"{obj}{suffix}"
                work_dir = f"{obj_dir}"

                # Добавление физики в '.inp' файлы
                inp_redactor(out_name, in_name,work_dir, delete_in_file = True, print_done = True)

                # Cчёт .inp файла в Abaqus
                start_abaqus_time = time.perf_counter()
                download_time = None
                count_time = None

                calc_proc = sp.Popen(
                    ['abaqus', 'job=Job-' + out_name, 'input=' + out_name +  '.inp', 'cpus=2', 'memory=7200mb',
                     'interactive', 'ask=off'],
                    cwd=work_dir,
                    shell=True,
                    stdout=sp.PIPE,
                    stderr=sp.STDOUT,
                    text=True,
                    bufsize=1
                )
                happy_end = False
                for line in calc_proc.stdout:
                    if 'Run standard.exe' in line:
                        download_time = time.perf_counter() - start_abaqus_time
                        print(f'Загрузка модели в Abaqus, время: {download_time:.2f} c')
                    elif 'completed' in line.lower():
                        count_time = time.perf_counter() - download_time - start_abaqus_time
                        print(f'Расчёт модели, время: {count_time:.2f} c')
                        happy_end = True
                calc_proc.wait()

                if not happy_end:
                    print('\nОшибка при подсчёте в Abaqus')

                # Удаление счётных файлов после расчёта
                PROTECTED_EXTS = {'.odb'}
                files_to_delete = [f for f in (OBJ_DIR / obj).iterdir() if
                                   f.is_file() and f.name.startswith('Job-' + out_name) and f.suffix.lower() not in PROTECTED_EXTS]
                for f in files_to_delete:
                    try:
                        f.unlink()
                        print(f'Удалён: {f.name}')
                    except Exception as e:
                        print(f'Ошибка при удалении {f.name}: {e}')

                #Парсинг файлов
                abaqus_pars = f'abaqus python abaqus_parsing.py "{obj_dir}"'
                sp.run(abaqus_pars, shell=True)


                elapsed = time.perf_counter() - start_abaqus_time
                print(f'\nРасчёт завершён, время: {elapsed:.2f} c\n')

        # После завершения работы над объектом направляем его в его папку
        shutil.move(f"{OBJ_DIR}/{obj}.obj",obj_dir)

        print(f"Время обработки '{obj}': {time.perf_counter() - start_obj_time:.2f}")

    print(f"\nВремя работы программы: {time.perf_counter() - start:.2f} c")