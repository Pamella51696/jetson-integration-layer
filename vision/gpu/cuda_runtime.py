"""Minimal libcudart bindings (JetPack ships libcudart; no PyCUDA required)."""
from __future__ import annotations

import ctypes
from typing import Optional

import numpy as np

_lib: Optional[ctypes.CDLL] = None


def _cuda() -> ctypes.CDLL:
    global _lib
    if _lib is not None:
        return _lib
    for name in ("libcudart.so", "libcudart.so.12", "libcudart.so.11.0"):
        try:
            _lib = ctypes.CDLL(name)
            return _lib
        except OSError:
            continue
    raise OSError("libcudart not found (CUDA runtime required for TensorRT)")


def malloc(nbytes: int) -> int:
    ptr = ctypes.c_void_p()
    err = _cuda().cudaMalloc(ctypes.byref(ptr), ctypes.c_size_t(nbytes))
    if err != 0:
        raise RuntimeError(f"cudaMalloc failed with code {err}")
    return int(ptr.value)


def free(ptr: int) -> None:
    if ptr:
        _cuda().cudaFree(ctypes.c_void_p(ptr))


def memcpy_htod(dst_ptr: int, host: np.ndarray) -> None:
    host = np.ascontiguousarray(host)
    err = _cuda().cudaMemcpy(
        ctypes.c_void_p(dst_ptr),
        host.ctypes.data_as(ctypes.c_void_p),
        ctypes.c_size_t(host.nbytes),
        ctypes.c_int(1),  # cudaMemcpyHostToDevice
    )
    if err != 0:
        raise RuntimeError(f"cudaMemcpy HtoD failed with code {err}")


def memcpy_dtoh(host: np.ndarray, src_ptr: int) -> None:
    host = np.ascontiguousarray(host)
    err = _cuda().cudaMemcpy(
        host.ctypes.data_as(ctypes.c_void_p),
        ctypes.c_void_p(src_ptr),
        ctypes.c_size_t(host.nbytes),
        ctypes.c_int(2),  # cudaMemcpyDeviceToHost
    )
    if err != 0:
        raise RuntimeError(f"cudaMemcpy DtoH failed with code {err}")
