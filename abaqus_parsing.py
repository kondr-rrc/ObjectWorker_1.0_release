from odbAccess import openOdb
import numpy as np
import sys
import os
import glob
import time

# ---- Параметры задачи ----
L0 = 5.0      # мм — ВНИМАНИЕ: сверьте с y.max()-y.min() в диагностике!
A0 = 25.0     # мм^2 (5×5 поперечное сечение ⟂ Y)
F  = 1000.0   # Н (*Cload)

# ---- Параметры обработки ----
Y_TOL_REL      = 1e-4      # относит. допуск на "плоскость" bottom
L0_CHECK_TOL   = 0.01      # 1% — предупреждать, если Y-диапазон != L0
EXPECTED_FILES = 3


def compute_E(odb_path):
    """Открывает ODB, возвращает (E_eff, u2_roof, u2_bottom, dL, y_range)."""
    odb = openOdb(odb_path)
    try:
        step  = odb.steps.values()[-1]
        frame = step.frames[-1]
        disp  = frame.fieldOutputs['U']

        # --- Roof через reference point ---
        rp_names = [n for n in odb.rootAssembly.nodeSets.keys()
                    if 'M_SET-7' in n.replace(' ', '')]
        if not rp_names:
            raise RuntimeError("M_SET-7 не найден. Доступные: {}".format(
                list(odb.rootAssembly.nodeSets.keys())))
        rp_set = odb.rootAssembly.nodeSets[rp_names[0]]
        rp_vals = disp.getSubset(region=rp_set).values
        if len(rp_vals) != 1:
            raise RuntimeError("M_SET-7 содержит {} узлов вместо 1".format(len(rp_vals)))
        u2_roof = rp_vals[0].data[1]

        # --- Bottom через координату Y ---
        inst   = odb.rootAssembly.instances['PART-1-1']
        coords = np.array([n.coordinates for n in inst.nodes])
        labels = np.array([n.label for n in inst.nodes])
        y      = coords[:, 1]
        y_tol  = Y_TOL_REL * (y.max() - y.min())
        bottom_labels = set(labels[np.abs(y - y.min()) < y_tol].tolist())

        inst_vals = disp.getSubset(region=inst).values
        u2_bottom = np.array([v.data[1] for v in inst_vals
                              if v.nodeLabel in bottom_labels])

        y_range = float(y.max() - y.min())
    finally:
        odb.close()

    if u2_bottom.size == 0:
        raise RuntimeError("bottom set пуст (проверьте Y_TOL_REL)")

    dL    = abs(u2_roof - u2_bottom.mean())
    eps   = dL / L0
    sig   = F / A0
    E_eff = sig / eps
    return E_eff, u2_roof, u2_bottom.mean(), dL, y_range, u2_bottom.size



def main():
    if len(sys.argv) < 2:
        print("Usage: abaqus python batch_E.py <folder>")
        sys.exit(1)

    folder = sys.argv[1]
    if not os.path.isdir(folder):
        print("Папка не найдена: {}".format(folder))
        sys.exit(1)

    odb_files = sorted(glob.glob(os.path.join(folder, '*.odb')))
    if not odb_files:
        print("ODB-файлы не найдены в: {}".format(folder))
        return

    folder_name = os.path.basename(os.path.normpath(os.path.abspath(folder)))
    txt_path = os.path.join(folder, '{}_E_results.txt'.format(folder_name))
    done_suffixes = set()

    # Читаем уже посчитанные суффиксы, если txt существует
    if os.path.exists(txt_path):
        with open(txt_path, 'r') as f:
            for line in f:
                parts = line.strip().split()
                if parts:
                    done_suffixes.add(parts[0])

    # Ищем первый ещё не посчитанный ODB-файл
    target_path = None
    target_suffix = None
    for path in odb_files:
        base_name = os.path.splitext(os.path.basename(path))[0]
        suffix = base_name.rsplit('_', 1)[-1]
        if suffix not in done_suffixes:
            target_path = path
            target_suffix = suffix
            break

    if target_path is None:
        print("Все ODB-файлы в папке уже посчитаны (суффиксы: {}).".format(
            ', '.join(sorted(done_suffixes))))
        return

    name = os.path.basename(target_path)
    t0 = time.time()

    print("\n" + "=" * 62)
    print("  Обработка: {} (суффикс: {})".format(name, target_suffix))
    print("-" * 62)

    try:
        E, u2r, u2b, dL, yr, n = compute_E(target_path)
        print("  Y-диапазон mesh    : {:.4f} мм  (L0 задан: {:.4f})".format(yr, L0))
        if abs(yr - L0) > L0_CHECK_TOL * L0:
            print("  WARNING: Y-диапазон != L0! Проверьте значение L0.")
        print("  Узлов на bottom    : {}".format(n))
        print("  U2 roof  (RP)      : {:.6e} мм".format(u2r))
        print("  U2 bottom (mean)   : {:.6e} мм".format(u2b))
        print("  dL                 : {:.6e} мм".format(dL))
        print("  E_eff              : {:.2f} МПа".format(E))

        # Дописываем результат в txt (создаст файл, если его ещё нет)
        with open(txt_path, 'a') as f:
            f.write("{} {:.6f}\n".format(target_suffix, E))

        print("-" * 62)
        print("  Сохранено в: {}".format(txt_path))
        print("  Время: {:.2f} c".format(time.time() - t0))
        print("=" * 62)
    except Exception as e:
        print("  ERROR: {}".format(e))
        sys.exit(1)


if __name__ == '__main__':
    main()