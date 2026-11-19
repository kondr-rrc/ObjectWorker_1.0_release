import numpy as np
import cv2
from scipy.ndimage import distance_transform_edt
from skimage.morphology import skeletonize

def mean_local_thickness(mask_body) -> float:
    pad = 25
    mask_pad = np.pad(mask_body, pad, mode='constant', constant_values=0)
    dist_map = distance_transform_edt(mask_pad)

    mask_bool = dist_map.astype(bool)
    skel = skeletonize(mask_bool)

    ridge = np.argwhere(skel)  # Координаты Body Ridge тела, хребет/скелет/медиальная ось

    radii = dist_map[skel]  # Список радиусов скелета
    order = np.argsort(-radii)  # Номера точек скелета где радиусы отсортированы по убыванию, т.е. radii[order[0]] <= radii[order[1]] ...

    H, W = dist_map.shape  # Размерность изображения
    thickness = np.zeros((H, W), dtype=np.float64)
    visited = np.zeros((H, W), dtype=bool)

    for k in order:  # Здесь k - это точка скелета
        r, c = ridge[k]  # Координаты точки скелета
        d = float(radii[k])  # Значение Distance в точке скелета

        # По сути мы сначала строим прямоугольник через r0, r1 (здесь r - row); c0, c1 (с - coll)
        r0, r1 = max(0, int(np.floor(r - d))), min(H, int(np.ceil(r + d)) + 1)
        c0, c1 = max(0, int(np.floor(c - d))), min(W, int(np.ceil(c + d)) + 1)
        rr, cc = np.ogrid[r0:r1, c0:c1]
        # И уже в прямоугольнике "чертим" диск
        disk = (rr - r) ** 2 + (cc - c) ** 2 <= d * d

        # Говорим какие области будем смотреть
        sub_t = thickness[r0:r1, c0:c1]
        sub_v = visited[r0:r1, c0:c1]

        # Сверяемся с закрашенными местами
        paint = disk & ~sub_v

        sub_t[paint] = 2.0 * d  # Красим места
        sub_v |= disk  # Отмечаем где закрасили

    body_thickness = np.zeros((H, W), dtype=np.float64)
    body_thickness[mask_bool] = thickness[mask_bool]
    unpainted = (mask_bool == True) & (thickness == 0)
    painted = (thickness > 0)

    # Ищем ближайший закрашенный пиксель для каждой дырки
    if np.any(unpainted):
        _, indices = distance_transform_edt(~painted, return_indices=True)
        body_thickness[unpainted] = thickness[indices[0][unpainted], indices[1][unpainted]]

    return body_thickness[mask_bool].mean()

def local_pore_and_inclusions_thickness(image_num: int, work_dir: str):
    image_name = fr"{work_dir}\\slice_{image_num}.png"
    img = cv2.imread(image_name, cv2.IMREAD_GRAYSCALE)

    mask_inclusion = (img == 255).astype(np.uint8)  # Фиолетовый - 0 (фон), Желтый - 1 (тело)
    mask_pore = (img == 0).astype(np.uint8)  # Фиолетовый - 1 (фон), Желтый - 0 (тело)

    mean_thickness_inclusion = mean_local_thickness(mask_inclusion)
    mean_thickness_pore = mean_local_thickness(mask_pore)

    return np.array([mean_thickness_inclusion, mean_thickness_pore])

# ТЕСТ: Сплошной круг (радиус 30) -> Ожидаем 60
# H, W = 200, 200
# y, x = np.ogrid[-100:100, -100:100]
#
# mask_circle = (x**2 + y**2 <= 30**2).astype(np.uint8)
# print(mean_local_thickness(mask_circle))