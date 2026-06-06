/*
 * Copyright (c) 2026 Bytedance, Inc.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 */

#include "bcc_dwarf_unwind.h"

#include <errno.h>
#include <new>

#ifdef HAVE_LIBGUNWINDER
extern "C" {
#include "gunwinder/unwinder.h"
}
#endif

struct bcc_dwarf_unwind_context {
#ifdef HAVE_LIBGUNWINDER
  struct gu_context *gu_ctx;
#endif
};
struct bcc_dwarf_unwind_result {};

namespace {

int set_errno_return(int err) {
  errno = err;
  return -err;
}

}  // namespace

extern "C" {

bool bcc_dwarf_unwind_supported(void) {
  return false;
}

int bcc_dwarf_unwind_context_new(
    const struct bcc_dwarf_unwind_options *options,
    struct bcc_dwarf_unwind_context **context) {
  if (context == nullptr)
    return set_errno_return(EINVAL);

  *context = nullptr;
  if (options != nullptr && options->size < sizeof(*options))
    return set_errno_return(EINVAL);

  struct bcc_dwarf_unwind_context *new_context =
      new (std::nothrow) bcc_dwarf_unwind_context();
  if (new_context == nullptr)
    return set_errno_return(ENOMEM);

#ifdef HAVE_LIBGUNWINDER
  struct gu_init_cfg cfg = {};
  new_context->gu_ctx = gu_init(&cfg);
  if (new_context->gu_ctx == nullptr) {
    delete new_context;
    return set_errno_return(errno != 0 ? errno : ENOMEM);
  }
#endif

  *context = new_context;
  return 0;
}

void bcc_dwarf_unwind_context_free(struct bcc_dwarf_unwind_context *context) {
  if (context == nullptr)
    return;

#ifdef HAVE_LIBGUNWINDER
  gu_cleanup(context->gu_ctx);
#endif
  delete context;
}

int bcc_dwarf_unwind_sample(struct bcc_dwarf_unwind_context *context,
                            const struct bcc_dwarf_unwind_sample *sample,
                            struct bcc_dwarf_unwind_result **result) {
  if (result == nullptr)
    return set_errno_return(EINVAL);

  *result = nullptr;
  if (context == nullptr || sample == nullptr ||
      sample->size < sizeof(*sample))
    return set_errno_return(EINVAL);

  return set_errno_return(ENOTSUP);
}

void bcc_dwarf_unwind_result_free(struct bcc_dwarf_unwind_result *result) {
  delete result;
}

}
