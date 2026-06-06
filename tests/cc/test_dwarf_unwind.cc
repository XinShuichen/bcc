/*
 * Copyright (c) 2026 Bytedance, Inc.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 */

#include <errno.h>

#include "bcc_dwarf_unwind.h"
#include "catch.hpp"

TEST_CASE("DWARF unwind ABI reports unsupported default behavior",
          "[dwarf_unwind]") {
  struct bcc_dwarf_unwind_context *ctx = nullptr;
  struct bcc_dwarf_unwind_result *result =
      reinterpret_cast<struct bcc_dwarf_unwind_result *>(0x1);
  struct bcc_dwarf_unwind_options options = {};
  struct bcc_dwarf_unwind_sample sample = {};

  options.size = sizeof(options);
  sample.size = sizeof(sample);
  sample.pid = 1;

  REQUIRE(bcc_dwarf_unwind_supported() == false);
  REQUIRE(bcc_dwarf_unwind_context_new(&options, &ctx) == 0);
  REQUIRE(ctx != nullptr);

  errno = 0;
  REQUIRE(bcc_dwarf_unwind_sample(ctx, &sample, &result) == -ENOTSUP);
  REQUIRE(errno == ENOTSUP);
  REQUIRE(result == nullptr);

  bcc_dwarf_unwind_result_free(nullptr);
  bcc_dwarf_unwind_context_free(nullptr);
  bcc_dwarf_unwind_context_free(ctx);
}
