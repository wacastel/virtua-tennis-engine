#!/usr/bin/env python3
"""Read-only execution observation for the pinned original SH4 laboratory."""
import hashlib
from pathlib import Path

REL = 'core/hw/sh4/interpr/sh4_interpreter.cpp'
PIN = 'ad864b0755f5be43a04b86e712e474ef88688f67799f67fcf95ea462a402b1b6'
HOOK = r'''
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <map>
#include <unordered_set>
#include <vector>
namespace {
struct VTSh4Trace {
    FILE *file = nullptr;
    std::string directory;
    std::unordered_set<uint64_t> seen;
    std::map<uint32_t, std::vector<uint8_t>> pages;
    std::map<uint32_t, unsigned> pageNumbers;
    unsigned serial = 0;
    VTSh4Trace() {
        const char *path = std::getenv("VT_SH4_TRACE");
        if (!path || !*path) return;
        directory = std::string(path) + ".data";
        std::filesystem::create_directories(directory);
        file = std::fopen(path, "wx");
        if (!file) throw std::runtime_error("Cannot create fresh SH4 trace");
    }
    ~VTSh4Trace() { if (file) std::fclose(file); }
    void instruction(Sh4Context *ctx, uint16_t word) {
        if (!file) return;
        const uint32_t pc = ctx->pc - 2;
        if (!seen.insert((uint64_t(pc) << 16) | word).second) return;
        const uint32_t physical = pc & 0x1fffffff;
        unsigned page = 0;
        if (physical >= 0x0c000000 && physical < 0x0e000000) {
            const uint32_t base = (physical & RAM_MASK) & ~uint32_t(4095);
            auto &prior = pages[base];
            if (prior.size() != 4096 || std::memcmp(prior.data(), &mem_b[base], 4096)) {
                prior.assign(&mem_b[base], &mem_b[base] + 4096);
                pageNumbers[base] = ++serial;
                const std::string name = "page-" + std::to_string(serial) + ".bin";
                FILE *out = std::fopen((directory + "/" + name).c_str(), "wx");
                if (!out) throw std::runtime_error("Cannot create SH4 page snapshot");
                bool ok = std::fwrite(prior.data(), 1, prior.size(), out) == prior.size();
                bool closed = std::fclose(out) == 0;
                if (!ok || !closed) throw std::runtime_error("Cannot save SH4 page snapshot");
                std::fprintf(file, "{\"kind\":\"page\",\"number\":%u,\"offset\":%u,\"bytes\":4096,\"file\":\"%s\"}\n", serial, base, name.c_str());
            }
            page = pageNumbers[base];
        }
        // The already fetched word is authoritative, even if the instruction
        // cache legitimately differs from backing RAM. No extra bus access.
        std::fprintf(file, "{\"kind\":\"instruction\",\"pc\":%u,\"opcode\":%u,\"page\":%u,\"sr\":%u,\"fpscr\":%u,\"mmu\":%s}\n", pc, word, page, ctx->sr.getFull(), ctx->fpscr.full, mmu_enabled() ? "true" : "false");
        if (std::fflush(file)) throw std::runtime_error("Cannot flush SH4 trace");
    }
};
VTSh4Trace &vt_sh4_trace() { static VTSh4Trace trace; return trace; }
}
'''


def sh4_patches(source):
    raw = (Path(source)/REL).read_bytes()
    if hashlib.sha256(raw).hexdigest() != PIN:
        raise RuntimeError('Unpinned SH4 observer source')
    text = raw.decode()
    anchor = 'void Sh4Interpreter::ExecuteOpcode(u16 op)\n{'
    if text.count(anchor) != 1:
        raise RuntimeError('SH4 observer entry anchor changed')
    text = text.replace(anchor, HOOK+'\n'+anchor+'\n\tvt_sh4_trace().instruction(ctx, op);', 1)
    return {REL: text}
