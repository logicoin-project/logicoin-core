// Logicoin / LOGIC CUDA Worker v0.12.15.3
// LogicHash-v2-CUDA-Mix
//
// v0.12.15.3 Streaming-Pipeline:
// - ein Worker-Prozess und ein CUDA-Kontext pro GPU
// - START startet intern viele kurze Kernel direkt hintereinander
// - Python muss nicht mehr jeden einzelnen Batch anfordern
// - kurze Kernel bleiben Windows-WDDM/TDR-sicher
//
// Befehle:
//   PING
//   SCAN <basehash> <difficulty_bits> <start_nonce> <count>
//   START <job_id> <basehash> <difficulty_bits> <start_nonce>
//         <chunk_count> <progress_ms> <duty_percent>
//   STOP <job_id>
//   QUIT
//
// Antworten:
//   READY 0.12.15.3 <device>
//   PONG
//   NONE <tested_exact> <active_ms>
//   FOUND <nonce> <hash> <tested_exact> <active_ms>
//   STARTED <job_id>
//   PROGRESS <job_id> <tested> <active_ms> <wall_ms> <next_nonce>
//   STREAM_FOUND <job_id> <nonce> <hash> <tested> <active_ms> <wall_ms>
//   STOPPED <job_id> <tested> <active_ms> <wall_ms> <next_nonce>
//   ERROR <message>
//   BYE

#include <cuda_runtime.h>

#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <mutex>
#include <sstream>
#include <string>
#include <thread>

#define MASK64 0xFFFFFFFFFFFFFFFFULL

struct FoundResult {
    int found;
    unsigned long long nonce;
    uint8_t hash[32];
    unsigned long long tested;
};

struct WorkerContext {
    int device;
    uint8_t* base_dev;
    FoundResult* result_dev;
    int threads;
    int blocks;
};

struct StreamController {
    std::atomic<bool> stop_requested;
    std::atomic<bool> running;
    std::thread thread;
    std::string job_id;

    StreamController()
        : stop_requested(false),
          running(false),
          job_id("") {
    }
};

std::mutex OUTPUT_MUTEX;

void write_line(const std::string& text) {
    std::lock_guard<std::mutex> lock(OUTPUT_MUTEX);
    std::cout << text << "\n";
    std::cout.flush();
}

__device__ unsigned long long read_u64_le(const uint8_t* p) {
    return ((unsigned long long)p[0]) |
           ((unsigned long long)p[1] << 8) |
           ((unsigned long long)p[2] << 16) |
           ((unsigned long long)p[3] << 24) |
           ((unsigned long long)p[4] << 32) |
           ((unsigned long long)p[5] << 40) |
           ((unsigned long long)p[6] << 48) |
           ((unsigned long long)p[7] << 56);
}

__device__ unsigned long long splitmix64_gpu(unsigned long long x) {
    x = (x + 0x9E3779B97F4A7C15ULL) & MASK64;
    x = ((x ^ (x >> 30)) * 0xBF58476D1CE4E5B9ULL) & MASK64;
    x = ((x ^ (x >> 27)) * 0x94D049BB133111EBULL) & MASK64;
    x = (x ^ (x >> 31)) & MASK64;
    return x;
}

__device__ void write_u64_be(uint8_t* out, unsigned long long x) {
    out[0] = (uint8_t)(x >> 56);
    out[1] = (uint8_t)(x >> 48);
    out[2] = (uint8_t)(x >> 40);
    out[3] = (uint8_t)(x >> 32);
    out[4] = (uint8_t)(x >> 24);
    out[5] = (uint8_t)(x >> 16);
    out[6] = (uint8_t)(x >> 8);
    out[7] = (uint8_t)(x);
}

__device__ bool has_leading_zero_bits(
    const uint8_t hash[32],
    int difficulty_bits
) {
    if (difficulty_bits <= 0) return true;
    if (difficulty_bits > 256) return false;

    int full_bytes = difficulty_bits / 8;
    int remaining_bits = difficulty_bits % 8;

    for (int i = 0; i < full_bytes; i++) {
        if (hash[i] != 0) return false;
    }

    if (remaining_bits == 0) return true;
    if (full_bytes >= 32) return false;

    uint8_t mask = (uint8_t)(0xFF << (8 - remaining_bits));
    return (hash[full_bytes] & mask) == 0;
}

__global__ void scan_kernel(
    const uint8_t* base_hash,
    unsigned long long start_nonce,
    unsigned long long count,
    int difficulty_bits,
    FoundResult* result
) {
    // Exakte Messung: Jeder Thread zählt nur tatsächlich berechnete Nonces.
    // Danach wird pro CUDA-Block reduziert und nur einmal global addiert.
    __shared__ unsigned long long tested_shared[256];

    unsigned long long idx =
        (unsigned long long)blockIdx.x * blockDim.x + threadIdx.x;
    unsigned long long stride =
        (unsigned long long)blockDim.x * gridDim.x;
    unsigned long long local_tested = 0ULL;

    unsigned long long s0 = read_u64_le(base_hash + 0);
    unsigned long long s1 = read_u64_le(base_hash + 8);
    unsigned long long s2 = read_u64_le(base_hash + 16);
    unsigned long long s3 = read_u64_le(base_hash + 24);

    for (
        unsigned long long offset = idx;
        offset < count;
        offset += stride
    ) {
        if (atomicAdd(&result->found, 0) != 0) {
            break;
        }

        unsigned long long nonce = start_nonce + offset;

        unsigned long long h0 =
            splitmix64_gpu(s0 ^ nonce ^ 0x243F6A8885A308D3ULL);
        unsigned long long h1 =
            splitmix64_gpu(s1 ^ nonce ^ 0x13198A2E03707344ULL);
        unsigned long long h2 =
            splitmix64_gpu(s2 ^ nonce ^ 0xA4093822299F31D0ULL);
        unsigned long long h3 =
            splitmix64_gpu(s3 ^ nonce ^ 0x082EFA98EC4E6C89ULL);

        uint8_t hash[32];
        write_u64_be(hash + 0, h0);
        write_u64_be(hash + 8, h1);
        write_u64_be(hash + 16, h2);
        write_u64_be(hash + 24, h3);

        local_tested++;

        if (has_leading_zero_bits(hash, difficulty_bits)) {
            if (atomicCAS(&result->found, 0, 1) == 0) {
                result->nonce = nonce;
                for (int i = 0; i < 32; i++) {
                    result->hash[i] = hash[i];
                }
            }
            break;
        }
    }

    tested_shared[threadIdx.x] = local_tested;
    __syncthreads();

    for (
        unsigned int step = blockDim.x / 2;
        step > 0;
        step >>= 1
    ) {
        if (threadIdx.x < step) {
            tested_shared[threadIdx.x] +=
                tested_shared[threadIdx.x + step];
        }
        __syncthreads();
    }

    if (threadIdx.x == 0) {
        atomicAdd(
            &result->tested,
            tested_shared[0]
        );
    }
}

bool hex_to_bytes(
    const std::string& hex,
    uint8_t* out,
    int expected_len
) {
    if ((int)hex.size() != expected_len * 2) {
        return false;
    }

    for (int i = 0; i < expected_len; i++) {
        std::string byte_str = hex.substr(i * 2, 2);
        char* end = nullptr;
        long value = std::strtol(
            byte_str.c_str(),
            &end,
            16
        );

        if (
            end == byte_str.c_str()
            || *end != '\0'
            || value < 0
            || value > 255
        ) {
            return false;
        }

        out[i] = (uint8_t)value;
    }

    return true;
}

std::string bytes_to_hex(const uint8_t* data, int len) {
    const char* hex = "0123456789abcdef";
    std::string out;
    out.reserve(len * 2);

    for (int i = 0; i < len; i++) {
        out.push_back(hex[(data[i] >> 4) & 0xF]);
        out.push_back(hex[data[i] & 0xF]);
    }

    return out;
}

const char* get_arg(
    int argc,
    char** argv,
    const char* name,
    const char* def = nullptr
) {
    for (int i = 1; i < argc - 1; i++) {
        if (std::strcmp(argv[i], name) == 0) {
            return argv[i + 1];
        }
    }

    return def;
}

bool has_flag(int argc, char** argv, const char* name) {
    for (int i = 1; i < argc; i++) {
        if (std::strcmp(argv[i], name) == 0) {
            return true;
        }
    }

    return false;
}

bool check_cuda(cudaError_t err, const char* operation) {
    if (err == cudaSuccess) {
        return true;
    }

    std::cerr
        << "ERROR CUDA "
        << operation
        << ": "
        << cudaGetErrorString(err)
        << "\n";
    std::cerr.flush();

    return false;
}

bool initialize_context(WorkerContext& ctx, int device) {
    std::memset(&ctx, 0, sizeof(ctx));
    ctx.device = device;
    ctx.threads = 256;
    ctx.blocks = 256;

    if (!check_cuda(cudaSetDevice(device), "set-device")) {
        return false;
    }

    if (!check_cuda(cudaFree(0), "context-init")) {
        return false;
    }

    if (!check_cuda(cudaMalloc(&ctx.base_dev, 32), "malloc-base")) {
        return false;
    }

    if (!check_cuda(
        cudaMalloc(&ctx.result_dev, sizeof(FoundResult)),
        "malloc-result"
    )) {
        cudaFree(ctx.base_dev);
        ctx.base_dev = nullptr;
        return false;
    }

    return true;
}

void destroy_context(WorkerContext& ctx) {
    if (ctx.base_dev != nullptr) {
        cudaFree(ctx.base_dev);
        ctx.base_dev = nullptr;
    }

    if (ctx.result_dev != nullptr) {
        cudaFree(ctx.result_dev);
        ctx.result_dev = nullptr;
    }
}

bool prepare_base(
    WorkerContext& ctx,
    const std::string& base_hex,
    std::string& error
) {
    uint8_t base_host[32];

    if (!hex_to_bytes(base_hex, base_host, 32)) {
        error = "invalid-base-hash";
        return false;
    }

    cudaError_t err = cudaMemcpy(
        ctx.base_dev,
        base_host,
        32,
        cudaMemcpyHostToDevice
    );

    if (err != cudaSuccess) {
        error =
            std::string("copy-base:")
            + cudaGetErrorString(err);
        return false;
    }

    return true;
}

bool run_prepared_scan(
    WorkerContext& ctx,
    int difficulty_bits,
    unsigned long long start_nonce,
    unsigned long long count,
    FoundResult& result_host,
    double& active_ms,
    std::string& error
) {
    if (difficulty_bits < 0 || difficulty_bits > 256) {
        error = "difficulty-bits-must-be-0..256";
        return false;
    }

    std::memset(&result_host, 0, sizeof(result_host));

    cudaError_t err = cudaMemcpy(
        ctx.result_dev,
        &result_host,
        sizeof(FoundResult),
        cudaMemcpyHostToDevice
    );

    if (err != cudaSuccess) {
        error =
            std::string("reset-result:")
            + cudaGetErrorString(err);
        return false;
    }

    if (count == 0) {
        active_ms = 0.0;
        return true;
    }

    auto active_started =
        std::chrono::steady_clock::now();

    scan_kernel<<<ctx.blocks, ctx.threads>>>(
        ctx.base_dev,
        start_nonce,
        count,
        difficulty_bits,
        ctx.result_dev
    );

    err = cudaDeviceSynchronize();

    auto active_finished =
        std::chrono::steady_clock::now();

    active_ms =
        std::chrono::duration<double, std::milli>(
            active_finished - active_started
        ).count();

    if (err != cudaSuccess) {
        error =
            std::string("kernel:")
            + cudaGetErrorString(err);
        return false;
    }

    err = cudaMemcpy(
        &result_host,
        ctx.result_dev,
        sizeof(FoundResult),
        cudaMemcpyDeviceToHost
    );

    if (err != cudaSuccess) {
        error =
            std::string("copy-result:")
            + cudaGetErrorString(err);
        return false;
    }

    return true;
}

bool run_scan(
    WorkerContext& ctx,
    const std::string& base_hex,
    int difficulty_bits,
    unsigned long long start_nonce,
    unsigned long long count,
    FoundResult& result_host,
    double& active_ms,
    std::string& error
) {
    if (!prepare_base(ctx, base_hex, error)) {
        return false;
    }

    return run_prepared_scan(
        ctx,
        difficulty_bits,
        start_nonce,
        count,
        result_host,
        active_ms,
        error
    );
}

void print_scan_result(
    const FoundResult& result,
    double active_ms
) {
    std::ostringstream output;

    if (result.found) {
        output
            << "FOUND "
            << result.nonce
            << " "
            << bytes_to_hex(result.hash, 32)
            << " "
            << result.tested
            << " "
            << std::fixed
            << std::setprecision(3)
            << active_ms;
    } else {
        output
            << "NONE "
            << result.tested
            << " "
            << std::fixed
            << std::setprecision(3)
            << active_ms;
    }

    write_line(output.str());
}

void stop_stream(StreamController& stream) {
    stream.stop_requested.store(true);

    if (stream.thread.joinable()) {
        stream.thread.join();
    }

    stream.running.store(false);
    stream.stop_requested.store(false);
}

void start_stream(
    WorkerContext& ctx,
    StreamController& stream,
    const std::string& job_id,
    const std::string& base_hex,
    int difficulty_bits,
    unsigned long long start_nonce,
    unsigned long long chunk_count,
    int progress_ms,
    int duty_percent
) {
    stop_stream(stream);

    stream.job_id = job_id;
    stream.stop_requested.store(false);
    stream.running.store(true);

    write_line("STARTED " + job_id);

    stream.thread = std::thread(
        [&ctx,
         &stream,
         job_id,
         base_hex,
         difficulty_bits,
         start_nonce,
         chunk_count,
         progress_ms,
         duty_percent]() mutable {
            cudaSetDevice(ctx.device);

            std::string error;

            if (!prepare_base(ctx, base_hex, error)) {
                write_line(
                    "ERROR stream-prepare:" + error
                );
                stream.running.store(false);
                return;
            }

            unsigned long long nonce = start_nonce;
            unsigned long long tested = 0ULL;
            double active_total_ms = 0.0;

            // Adaptive Chunkgröße:
            // Ziel sind etwa 12 ms Kernelzeit. Das reduziert die vielen
            // cudaDeviceSynchronize-/Memcpy-Lücken der alten 262k-Chunks,
            // bleibt aber deutlich unter typischen Windows-TDR-Grenzen.
            unsigned long long current_chunk_count =
                chunk_count;
            const unsigned long long min_chunk_count =
                1ULL << 20;
            const unsigned long long max_chunk_count =
                1ULL << 27;
            const double target_kernel_ms = 12.0;

            auto wall_started =
                std::chrono::steady_clock::now();
            auto last_progress = wall_started;

            while (!stream.stop_requested.load()) {
                FoundResult result;
                double active_ms = 0.0;
                unsigned long long scanned_count =
                    current_chunk_count;

                if (!run_prepared_scan(
                    ctx,
                    difficulty_bits,
                    nonce,
                    scanned_count,
                    result,
                    active_ms,
                    error
                )) {
                    write_line(
                        "ERROR stream-scan:" + error
                    );
                    break;
                }

                tested += result.tested;
                active_total_ms += active_ms;

                auto now =
                    std::chrono::steady_clock::now();
                double wall_ms =
                    std::chrono::duration<double, std::milli>(
                        now - wall_started
                    ).count();

                if (result.found) {
                    std::ostringstream output;
                    output
                        << "STREAM_FOUND "
                        << job_id
                        << " "
                        << result.nonce
                        << " "
                        << bytes_to_hex(result.hash, 32)
                        << " "
                        << tested
                        << " "
                        << std::fixed
                        << std::setprecision(3)
                        << active_total_ms
                        << " "
                        << wall_ms;
                    write_line(output.str());
                    stream.running.store(false);
                    return;
                }

                nonce += scanned_count;

                if (active_ms > 0.05) {
                    double ratio =
                        target_kernel_ms / active_ms;
                    ratio = ratio < 0.5
                        ? 0.5
                        : (ratio > 2.0 ? 2.0 : ratio);

                    unsigned long long next_chunk =
                        (unsigned long long)(
                            (double)current_chunk_count
                            * ratio
                        );

                    if (next_chunk < min_chunk_count) {
                        next_chunk = min_chunk_count;
                    }
                    if (next_chunk > max_chunk_count) {
                        next_chunk = max_chunk_count;
                    }

                    current_chunk_count = next_chunk;
                }

                if (duty_percent < 100) {
                    double sleep_ms =
                        active_ms
                        * (
                            (100.0 - duty_percent)
                            / (double)duty_percent
                        );

                    if (sleep_ms > 0.0) {
                        std::this_thread::sleep_for(
                            std::chrono::duration<double, std::milli>(
                                sleep_ms
                            )
                        );
                    }
                }

                now = std::chrono::steady_clock::now();

                if (
                    std::chrono::duration_cast<
                        std::chrono::milliseconds
                    >(now - last_progress).count()
                    >= progress_ms
                ) {
                    double current_wall_ms =
                        std::chrono::duration<double, std::milli>(
                            now - wall_started
                        ).count();

                    std::ostringstream output;
                    output
                        << "PROGRESS "
                        << job_id
                        << " "
                        << tested
                        << " "
                        << std::fixed
                        << std::setprecision(3)
                        << active_total_ms
                        << " "
                        << current_wall_ms
                        << " "
                        << nonce;
                    write_line(output.str());

                    last_progress = now;
                }
            }

            auto stopped_at =
                std::chrono::steady_clock::now();
            double wall_ms =
                std::chrono::duration<double, std::milli>(
                    stopped_at - wall_started
                ).count();

            std::ostringstream output;
            output
                << "STOPPED "
                << job_id
                << " "
                << tested
                << " "
                << std::fixed
                << std::setprecision(3)
                << active_total_ms
                << " "
                << wall_ms
                << " "
                << nonce;
            write_line(output.str());

            stream.running.store(false);
        }
    );
}

int run_server(int device) {
    WorkerContext ctx;

    if (!initialize_context(ctx, device)) {
        return 3;
    }

    StreamController stream;

    write_line(
        "READY 0.12.15.3 "
        + std::to_string(device)
    );

    std::string line;

    while (std::getline(std::cin, line)) {
        if (line.empty()) {
            continue;
        }

        if (line == "PING") {
            write_line("PONG");
            continue;
        }

        if (line == "QUIT") {
            stop_stream(stream);
            write_line("BYE");
            destroy_context(ctx);
            return 0;
        }

        std::istringstream command(line);
        std::string operation;
        command >> operation;

        if (operation == "STOP") {
            std::string job_id;
            command >> job_id;
            stop_stream(stream);

            if (!job_id.empty()) {
                write_line("STOP_ACK " + job_id);
            } else {
                write_line("STOP_ACK");
            }
            continue;
        }

        if (operation == "START") {
            std::string job_id;
            std::string base_hex;
            int difficulty_bits = 0;
            unsigned long long start_nonce = 0;
            unsigned long long chunk_count = 262144;
            int progress_ms = 250;
            int duty_percent = 100;

            command
                >> job_id
                >> base_hex
                >> difficulty_bits
                >> start_nonce
                >> chunk_count
                >> progress_ms
                >> duty_percent;

            if (
                job_id.empty()
                || base_hex.empty()
                || command.fail()
            ) {
                write_line(
                    "ERROR invalid-start-command"
                );
                continue;
            }

            chunk_count = (
                chunk_count < 8192
                ? 8192
                : chunk_count
            );
            progress_ms = (
                progress_ms < 50
                ? 50
                : progress_ms
            );
            duty_percent = (
                duty_percent < 5
                ? 5
                : (
                    duty_percent > 100
                    ? 100
                    : duty_percent
                )
            );

            start_stream(
                ctx,
                stream,
                job_id,
                base_hex,
                difficulty_bits,
                start_nonce,
                chunk_count,
                progress_ms,
                duty_percent
            );
            continue;
        }

        if (operation == "SCAN") {
            stop_stream(stream);

            std::string base_hex;
            int difficulty_bits = 0;
            unsigned long long start_nonce = 0;
            unsigned long long count = 0;

            command
                >> base_hex
                >> difficulty_bits
                >> start_nonce
                >> count;

            if (
                base_hex.empty()
                || command.fail()
            ) {
                write_line(
                    "ERROR invalid-scan-command"
                );
                continue;
            }

            FoundResult result;
            double active_ms = 0.0;
            std::string error;

            if (!run_scan(
                ctx,
                base_hex,
                difficulty_bits,
                start_nonce,
                count,
                result,
                active_ms,
                error
            )) {
                write_line("ERROR " + error);
                continue;
            }

            print_scan_result(result, active_ms);
            continue;
        }

        write_line("ERROR unknown-command");
    }

    stop_stream(stream);
    destroy_context(ctx);
    return 0;
}

int run_benchmark(
    int device,
    const std::string& base_hex,
    unsigned long long start_nonce,
    unsigned long long count,
    int benchmark_ms
) {
    WorkerContext ctx;

    if (!initialize_context(ctx, device)) {
        return 3;
    }

    std::string error;

    if (!prepare_base(ctx, base_hex, error)) {
        std::cerr << "ERROR " << error << "\n";
        destroy_context(ctx);
        return 2;
    }

    double total_ms = 0.0;
    unsigned long long total_hashes = 0ULL;
    unsigned long long round_start = start_nonce;

    while (total_ms < (double)benchmark_ms) {
        FoundResult result;
        double active_ms = 0.0;

        if (!run_prepared_scan(
            ctx,
            256,
            round_start,
            count,
            result,
            active_ms,
            error
        )) {
            std::cerr << "ERROR " << error << "\n";
            destroy_context(ctx);
            return 4;
        }

        total_ms += active_ms;
        total_hashes += result.tested;
        round_start += count;
    }

    destroy_context(ctx);

    double hps =
        total_ms > 0.0
        ? ((double)total_hashes / (total_ms / 1000.0))
        : 0.0;

    std::cout
        << "BENCH "
        << total_hashes
        << " "
        << std::fixed
        << std::setprecision(3)
        << total_ms
        << " "
        << std::fixed
        << std::setprecision(3)
        << hps
        << "\n";

    return 0;
}

int main(int argc, char** argv) {
    if (
        has_flag(argc, argv, "--worker-version")
        || has_flag(argc, argv, "--version")
    ) {
        std::cout
            << "Logicoin CUDA Worker v0.12.15.3 "
            << "LogicHash-v2-CUDA-Mix Streaming\n";
        return 0;
    }

    int device = std::atoi(
        get_arg(argc, argv, "--device", "0")
    );

    if (has_flag(argc, argv, "--server")) {
        return run_server(device);
    }

    const char* base_hex_arg =
        get_arg(argc, argv, "--base-hash");
    const char* diff_arg =
        get_arg(argc, argv, "--difficulty", "16");
    const char* start_arg =
        get_arg(argc, argv, "--start", "0");
    const char* count_arg =
        get_arg(argc, argv, "--count", "262144");
    const char* benchmark_ms_arg =
        get_arg(argc, argv, "--benchmark-ms", "0");

    if (!base_hex_arg) {
        std::cerr << "ERROR missing --base-hash\n";
        return 2;
    }

    int difficulty_bits = std::atoi(diff_arg);
    unsigned long long start_nonce =
        std::strtoull(start_arg, nullptr, 10);
    unsigned long long count =
        std::strtoull(count_arg, nullptr, 10);
    int benchmark_ms =
        std::atoi(benchmark_ms_arg);

    if (benchmark_ms > 0) {
        return run_benchmark(
            device,
            std::string(base_hex_arg),
            start_nonce,
            count,
            benchmark_ms
        );
    }

    WorkerContext ctx;

    if (!initialize_context(ctx, device)) {
        return 3;
    }

    FoundResult result;
    double active_ms = 0.0;
    std::string error;

    bool ok = run_scan(
        ctx,
        std::string(base_hex_arg),
        difficulty_bits,
        start_nonce,
        count,
        result,
        active_ms,
        error
    );

    destroy_context(ctx);

    if (!ok) {
        std::cerr
            << "ERROR "
            << error
            << "\n";
        return 4;
    }

    print_scan_result(result, active_ms);
    return 0;
}
