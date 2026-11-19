import numpy as np
from pathlib import Path
import subprocess as sp
import time

rng = np.random.default_rng()

WORK_DIR = r"C:\Users\User\Desktop\Object Worker"
OBJ_DIR = Path(WORK_DIR) / "OBJECT_FOLDER"

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

# pore_txt_to_point_map('bone228_mean_pore')

pore_var_list = ['bone228_mean_pore', 'bone228_porosity']
sample_num = 1
for i in range(1, sample_num+1):
    for pore_variant in pore_var_list:
        #Создание биконта
        print(f"Создание биконта '{pore_variant}' № {i}\n")
        make_bicont_from_point_map(pore_variant)

