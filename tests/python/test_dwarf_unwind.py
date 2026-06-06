#!/usr/bin/env python3
# Copyright (c) 2026 Bytedance, Inc.
# Licensed under the Apache License, Version 2.0 (the "License")

import ctypes as ct
import errno
from unittest import TestCase, main, skipUnless

from bcc.dwarf import (
    BCC_DWARF_UNWIND_REASON_PROCESS_EXIT,
    GU_ARCH_NATIVE,
    GU_REGS_MAX_DWARF_REGS,
    GU_REGS_VERSION,
    DwarfUnwindError,
    DwarfUnwinder,
    GuRegs,
)
from bcc.libbcc import (
    bcc_dwarf_unwind_elf,
    bcc_dwarf_unwind_frame,
    bcc_dwarf_unwind_options,
    bcc_dwarf_unwind_result,
    bcc_dwarf_unwind_sample,
    lib,
)


class TestDwarfUnwind(TestCase):
    def test_ctypes_layout_is_sized_for_c_abi(self):
        options = bcc_dwarf_unwind_options()
        sample = bcc_dwarf_unwind_sample()
        frame = bcc_dwarf_unwind_frame()
        elf = bcc_dwarf_unwind_elf()
        result = bcc_dwarf_unwind_result()

        self.assertEqual(options.size, ct.sizeof(options))
        self.assertEqual(sample.size, ct.sizeof(sample))
        self.assertEqual(frame.size, ct.sizeof(frame))
        self.assertEqual(elf.size, ct.sizeof(elf))
        self.assertEqual(result.size, ct.sizeof(result))
        self.assertEqual(GU_REGS_VERSION, 1)
        self.assertEqual(GU_REGS_MAX_DWARF_REGS, 64)

    def test_context_lifecycle_is_idempotent(self):
        self.assertEqual(DwarfUnwinder.supported(),
                         lib.bcc_dwarf_unwind_supported())

        unwinder = DwarfUnwinder()
        unwinder.close()
        unwinder.close()

        with DwarfUnwinder() as scoped:
            self.assertIsNotNone(scoped)

    def test_sample_reports_unsupported_or_invalid_input(self):
        regs = GuRegs()
        regs.set(0, 0x1234)

        with DwarfUnwinder() as unwinder:
            if not DwarfUnwinder.supported():
                with self.assertRaises(DwarfUnwindError) as cm:
                    unwinder.sample(pid=1, regs=regs, stack_data=b"\0" * 64)
                self.assertEqual(cm.exception.errno, errno.ENOTSUP)
                return

            with self.assertRaises(DwarfUnwindError) as cm:
                unwinder.sample(pid=0, regs=regs, stack_data=b"\0" * 64)
            self.assertEqual(cm.exception.errno, errno.EINVAL)

    @skipUnless(lib.bcc_dwarf_unwind_supported(),
                "requires enabled DWARF unwinder")
    def test_enabled_sample_returns_owned_python_result(self):
        regs = GuRegs()

        with DwarfUnwinder() as unwinder:
            result = unwinder.sample(pid=99999999, regs=regs,
                                     stack_data=b"\0" * 64,
                                     max_frames=8)

        self.assertLess(result.unwind_ret, 0)
        self.assertEqual(result.stop_reason,
                         BCC_DWARF_UNWIND_REASON_PROCESS_EXIT)
        self.assertEqual(result.frames, [])

    def test_gu_regs_exposes_register_mask(self):
        regs = GuRegs(arch=GU_ARCH_NATIVE)

        self.assertTrue(regs.set(16, 0xfeedface))
        self.assertEqual(regs.get(16), 0xfeedface)
        self.assertIsNone(regs.get(17))
        self.assertFalse(regs.set(GU_REGS_MAX_DWARF_REGS, 1))


if __name__ == "__main__":
    main()
