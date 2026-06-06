# Copyright (c) 2026 Bytedance, Inc.
# Licensed under the Apache License, Version 2.0 (the "License")

import ctypes as ct
import errno
import os

from .libbcc import (
    BCC_DWARF_UNWIND_REASON_ARCH_FAIL,
    BCC_DWARF_UNWIND_REASON_CFI_FAIL,
    BCC_DWARF_UNWIND_REASON_CFI_FRAME_CFA_CALC_FAILED,
    BCC_DWARF_UNWIND_REASON_CFI_FRAME_CFA_FAILED,
    BCC_DWARF_UNWIND_REASON_CFI_FRAME_DECODE_FAILED,
    BCC_DWARF_UNWIND_REASON_END_OF_STACK,
    BCC_DWARF_UNWIND_REASON_FRAME_LIMIT,
    BCC_DWARF_UNWIND_REASON_LANG_SKIP,
    BCC_DWARF_UNWIND_REASON_NO_CFI,
    BCC_DWARF_UNWIND_REASON_NO_ELF,
    BCC_DWARF_UNWIND_REASON_NO_EXEC_PC,
    BCC_DWARF_UNWIND_REASON_NO_REGS,
    BCC_DWARF_UNWIND_REASON_OK,
    BCC_DWARF_UNWIND_REASON_PROCESS_EXIT,
    BCC_DWARF_UNWIND_REASON_STACK_READ_OUT_OF_RANGE,
    BCC_DWARF_UNWIND_REASON_TRUNCATED,
    BCC_DWARF_UNWIND_REASON_UNKNOWN,
    GU_ARCH_ARM64,
    GU_ARCH_NATIVE,
    GU_ARCH_X86_64,
    GU_REGS_MAX_DWARF_REGS,
    GU_REGS_VERSION,
    bcc_dwarf_unwind_options,
    bcc_dwarf_unwind_result,
    bcc_dwarf_unwind_sample,
    gu_regs,
    lib,
)


class DwarfUnwindError(OSError):
    def __init__(self, err, operation):
        OSError.__init__(self, err, os.strerror(err), operation)


class GuRegs(gu_regs):
    def set(self, dwarf_regno, value):
        if dwarf_regno < 0 or dwarf_regno >= GU_REGS_MAX_DWARF_REGS:
            return False
        self.dwarf[dwarf_regno] = value
        self.valid_mask |= 1 << dwarf_regno
        return True

    def get(self, dwarf_regno):
        if dwarf_regno < 0 or dwarf_regno >= GU_REGS_MAX_DWARF_REGS:
            return None
        if (self.valid_mask & (1 << dwarf_regno)) == 0:
            return None
        return self.dwarf[dwarf_regno]


class DwarfUnwindElf(object):
    def __init__(self, base_name=None, elf_file_path=None,
                 debug_file_path=None, build_id=b"", golang=False):
        self.base_name = base_name
        self.elf_file_path = elf_file_path
        self.debug_file_path = debug_file_path
        self.build_id = build_id
        self.golang = golang


class DwarfUnwindFrame(object):
    def __init__(self, pc=0, abs_pc=0, offset=0, symbol=None, flags=0,
                 elf=None):
        self.pc = pc
        self.abs_pc = abs_pc
        self.offset = offset
        self.symbol = symbol
        self.flags = flags
        self.elf = elf if elf is not None else DwarfUnwindElf()


class DwarfUnwindResult(object):
    def __init__(self, unwind_ret=0, stop_reason=0, frames=None):
        self.unwind_ret = unwind_ret
        self.stop_reason = stop_reason
        self.frames = frames if frames is not None else []


class DwarfUnwinder(object):
    def __init__(self, flags=0):
        self._context = None
        options = bcc_dwarf_unwind_options()
        options.flags = flags
        context = ct.c_void_p()
        ret = lib.bcc_dwarf_unwind_context_new(ct.byref(options),
                                               ct.byref(context))
        if ret < 0:
            _raise_from_errno(ret, "bcc_dwarf_unwind_context_new")
        self._context = context

    @staticmethod
    def supported():
        return lib.bcc_dwarf_unwind_supported()

    def close(self):
        if self._context:
            lib.bcc_dwarf_unwind_context_free(self._context)
            self._context = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def __del__(self):
        self.close()

    def sample(self, pid, regs, stack_data=None, unique_id=0, ustack_fp=None,
               max_frames=0, flags=0):
        if self._context is None:
            raise DwarfUnwindError(errno.EINVAL, "bcc_dwarf_unwind_sample")
        sample, keepalive = _build_sample(pid, regs, stack_data, unique_id,
                                          ustack_fp, max_frames, flags)
        result = ct.POINTER(bcc_dwarf_unwind_result)()
        ret = lib.bcc_dwarf_unwind_sample(self._context, ct.byref(sample),
                                          ct.byref(result))
        if ret < 0:
            _raise_from_errno(ret, "bcc_dwarf_unwind_sample")
        if not result:
            raise DwarfUnwindError(errno.EINVAL, "bcc_dwarf_unwind_sample")
        try:
            return _copy_result(result.contents)
        finally:
            lib.bcc_dwarf_unwind_result_free(result)


def _build_sample(pid, regs, stack_data, unique_id, ustack_fp, max_frames,
                  flags):
    if not isinstance(regs, gu_regs):
        raise TypeError("regs must be a GuRegs instance")

    keepalive = [regs]
    sample = bcc_dwarf_unwind_sample()
    sample.flags = flags
    sample.pid = pid
    sample.unique_id = unique_id
    sample.regs = ct.cast(ct.byref(regs), ct.c_void_p)
    sample.max_frames = max_frames

    if stack_data is not None:
        stack = (ct.c_uint8 * len(stack_data)).from_buffer_copy(stack_data)
        keepalive.append(stack)
        sample.stack_data = stack
        sample.stack_size = len(stack_data)

    if ustack_fp is not None:
        fp = (ct.c_uint64 * len(ustack_fp))(*ustack_fp)
        keepalive.append(fp)
        sample.ustack_fp = fp
        sample.ustack_fp_level = len(ustack_fp)

    return sample, keepalive


def _copy_result(result):
    frames = []
    for i in range(result.frame_count):
        frames.append(_copy_frame(result.frames[i]))
    return DwarfUnwindResult(result.unwind_ret, result.stop_reason, frames)


def _copy_frame(frame):
    elf = frame.elf
    build_id = b""
    if elf.build_id and elf.build_id_len:
        build_id = bytes(bytearray(elf.build_id[:elf.build_id_len]))

    return DwarfUnwindFrame(
        pc=frame.pc,
        abs_pc=frame.abs_pc,
        offset=frame.offset,
        symbol=_decode(frame.symbol),
        flags=frame.flags,
        elf=DwarfUnwindElf(
            base_name=_decode(elf.base_name),
            elf_file_path=_decode(elf.elf_file_path),
            debug_file_path=_decode(elf.debug_file_path),
            build_id=build_id,
            golang=elf.golang))


def _decode(value):
    if value is None:
        return None
    return value.decode("utf-8", "replace")


def _raise_from_errno(ret, operation):
    err = ct.get_errno()
    if err == 0 and ret < 0:
        err = -ret
    raise DwarfUnwindError(err, operation)
