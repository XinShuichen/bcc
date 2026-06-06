/*
 * Copyright (c) 2026 Bytedance, Inc.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 */

#ifndef BCC_DWARF_UNWIND_H
#define BCC_DWARF_UNWIND_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <sys/types.h>

#ifdef __cplusplus
extern "C" {
#endif

struct bcc_dwarf_unwind_context;
struct bcc_dwarf_unwind_result;

struct bcc_dwarf_unwind_options {
  uint32_t size;
  uint32_t flags;
};

struct bcc_dwarf_unwind_sample {
  uint32_t size;
  uint32_t flags;
  pid_t pid;
};

bool bcc_dwarf_unwind_supported(void);
int bcc_dwarf_unwind_context_new(
    const struct bcc_dwarf_unwind_options *options,
    struct bcc_dwarf_unwind_context **context);
void bcc_dwarf_unwind_context_free(struct bcc_dwarf_unwind_context *context);
int bcc_dwarf_unwind_sample(struct bcc_dwarf_unwind_context *context,
                            const struct bcc_dwarf_unwind_sample *sample,
                            struct bcc_dwarf_unwind_result **result);
void bcc_dwarf_unwind_result_free(struct bcc_dwarf_unwind_result *result);

#ifdef __cplusplus
}
#endif

#endif
