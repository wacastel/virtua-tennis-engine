#!/usr/bin/env python3
"""Observation-only ARM/AICA patches for the pinned original Flycast laboratory.

sound_patches(source) returns relative source paths and derived text. It never
writes the original tree. VT_ARM7_TRACE and VT_AICADSP_TRACE name fresh JSONL
files; binary snapshots live beside each file in <trace-path>.data/. No hardware
read handler or instruction operation is added or replaced.
"""
import hashlib
from pathlib import Path

PINS = {
    'core/hw/arm7/arm7.cpp': '9a1e0193312f54d4de27bf5ff688917c28bb23ef991611e5426b1b50be802074',
    'core/hw/aica/dsp.cpp': '73f68333463766d6ebcf17484570525d9ed9425b98a06fcf35b64dfd9538bf06',
}

COMMON = r'''
#include <array>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <map>
#include <unordered_set>
#include <vector>

namespace {
struct VTTraceFile {
    FILE *file = nullptr;
    std::string directory;
    explicit VTTraceFile(const char *environment) {
        const char *path = std::getenv(environment);
        if (!path || !*path) return;
        directory = std::string(path) + ".data";
        std::filesystem::create_directories(directory);
        file = std::fopen(path, "wx");
        if (!file) throw std::runtime_error("Cannot create fresh sound trace");
    }
    ~VTTraceFile() { if (file) std::fclose(file); }
    void binary(const std::string &name, const void *bytes, size_t size) {
        FILE *out = std::fopen((directory + "/" + name).c_str(), "wx");
        if (!out) throw std::runtime_error("Cannot create sound snapshot");
        const bool ok = std::fwrite(bytes, 1, size, out) == size;
        const bool closed = std::fclose(out) == 0;
        if (!ok || !closed) throw std::runtime_error("Cannot save sound snapshot");
    }
    void flush() {
        if (std::fflush(file)) throw std::runtime_error("Cannot flush sound trace");
    }
};
}
'''

ARM = r'''
namespace {
struct VTArmTrace : VTTraceFile {
    std::unordered_set<uint64_t> seen;
    std::map<uint32_t, std::vector<uint8_t>> pages;
    std::map<uint32_t, unsigned> pageNumbers;
    unsigned pageSerial = 0, uploadSerial = 0;
    VTArmTrace() : VTTraceFile("VT_ARM7_TRACE") {}
    void instruction(uint32_t pc, uint32_t word) {
        if (!file || !seen.insert((uint64_t(pc) << 32) | word).second) return;
        const uint32_t base = (pc & ARAM_MASK) & ~uint32_t(4095);
        auto &prior = pages[base];
        if (prior.size() != 4096 || std::memcmp(prior.data(), &aica::aica_ram[base], 4096)) {
            prior.assign(&aica::aica_ram[base], &aica::aica_ram[base] + 4096);
            pageNumbers[base] = ++pageSerial;
            const std::string name = "page-" + std::to_string(pageSerial) + ".bin";
            binary(name, prior.data(), prior.size());
            std::fprintf(file, "{\"kind\":\"page\",\"number\":%u,\"offset\":%u,\"bytes\":4096,\"file\":\"%s\"}\n", pageSerial, base, name.c_str());
        }
        std::fprintf(file, "{\"kind\":\"instruction\",\"pc\":%u,\"opcode\":%u,\"soundOffset\":%u,\"page\":%u,\"armMode\":%d}\n", pc, word, pc & ARAM_MASK, pageNumbers[base], aica::arm::armMode);
        flush();
    }
    void enable(bool value, bool previous) {
        if (!file || value == previous) return;
        const std::string name = "upload-" + std::to_string(++uploadSerial) + ".bin";
        binary(name, &aica::aica_ram[0], ARAM_SIZE);
        std::fprintf(file, "{\"kind\":\"enable\",\"enabled\":%s,\"bytes\":%u,\"file\":\"%s\"}\n", value ? "true" : "false", unsigned(ARAM_SIZE), name.c_str());
        flush();
    }
};
VTArmTrace &vt_arm_trace() { static VTArmTrace trace; return trace; }
}
'''

DSP = r'''
namespace {
struct VTDSPTrace : VTTraceFile {
    std::map<std::array<uint32_t,512>, unsigned> images;
    std::array<uint32_t,512> previous{};
    unsigned current = 0;
    VTDSPTrace() : VTTraceFile("VT_AICADSP_TRACE") {}
    void observe(const char *kind, uint32_t address) {
        if (!file) return;
        const auto *mpro = aica::DSPData->MPRO;
        if (!current || std::memcmp(previous.data(), mpro, sizeof(previous))) {
            std::memcpy(previous.data(), mpro, sizeof(previous));
            auto [found, added] = images.emplace(previous, unsigned(images.size()+1));
            current = found->second;
            if (added) {
                const std::string name = "program-" + std::to_string(current) + ".bin";
                binary(name, previous.data(), sizeof(previous));
                std::fprintf(file, "{\"kind\":\"program\",\"number\":%u,\"bytes\":2048,\"file\":\"%s\"}\n", current, name.c_str());
            }
        }
        std::fprintf(file, "{\"kind\":\"%s\",\"address\":%u,\"program\":%u,\"dirty\":%s,\"stopped\":%s}\n", kind, address, current, aica::dsp::state.dirty ? "true" : "false", aica::dsp::state.stopped ? "true" : "false");
        flush();
    }
};
VTDSPTrace &vt_dsp_trace() { static VTDSPTrace trace; return trace; }
}
'''


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise RuntimeError('Pinned sound observer anchor changed: '+old[:80])
    return text.replace(old, new, 1)


def sound_patches(source):
    source = Path(source)
    original = {}
    for name, digest in PINS.items():
        raw = (source/name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise RuntimeError('Unpinned sound observer input: '+name)
        original[name] = raw.decode()
    arm = original['core/hw/arm7/arm7.cpp']
    arm = replace_once(arm, '#include "arm7_rec.h"', '#include "arm7_rec.h"\n'+COMMON+ARM)
    arm = replace_once(arm, '\t\treg[15].I = armNextPC + 8;',
        '\t\tvt_arm_trace().instruction(armNextPC, CPUReadMemoryQuick(armNextPC));\n\t\treg[15].I = armNextPC + 8;')
    arm = replace_once(arm, 'void enable(bool enabled)\n{',
        'void enable(bool enabled)\n{\n\tvt_arm_trace().enable(enabled, Arm7Enabled);')
    dsp = original['core/hw/aica/dsp.cpp']
    dsp = replace_once(dsp, '#include "aica.h"', '#include "aica.h"\n'+COMMON+DSP)
    dsp = replace_once(dsp, 'void writeProg(u32 addr)\n{',
        'void writeProg(u32 addr)\n{\n\tif (addr >= 0x3400 && addr < 0x3C00) vt_dsp_trace().observe("write", addr);')
    dsp = replace_once(dsp, 'void step()\n{',
        'void step()\n{\n\tif (state.dirty) vt_dsp_trace().observe("sample-program", 0);')
    return {'core/hw/arm7/arm7.cpp': arm, 'core/hw/aica/dsp.cpp': dsp}


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    a = p.parse_args()
    for name, text in sound_patches(a.source).items():
        print(name, len(text), hashlib.sha256(text.encode()).hexdigest())
