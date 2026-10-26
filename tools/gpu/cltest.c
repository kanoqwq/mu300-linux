/*
 * cltest: probe the Mali GPU through Android's OpenCL driver and run a vector addition.
 * Built for bionic without the NDK (see build.sh); runs inside the Android vendor chroot.
 *   cltest              probe and one verified vector addition
 *   cltest loop [SEC]   GPU stress: run a heavy kernel repeatedly for SEC seconds (0 = until killed),
 *                       printing the throughput every second
 * SPDX-License-Identifier: MIT
 */
#include <stddef.h>
#include <stdint.h>

int printf(const char *, ...);
int atoi(const char *);
int strcmp(const char *, const char *);
int fflush(void *);
void *malloc(size_t);
struct timespec { long tv_sec; long tv_nsec; };
int clock_gettime(int, struct timespec *);

typedef int32_t cl_int;
typedef uint32_t cl_uint;
typedef uint64_t cl_ulong;
typedef uint64_t cl_bitfield;
typedef void *cl_platform_id, *cl_device_id, *cl_context, *cl_command_queue, *cl_mem, *cl_program, *cl_kernel;
#define CL_DEVICE_TYPE_GPU (1 << 2)
#define CL_PLATFORM_NAME 0x0902
#define CL_PLATFORM_VERSION 0x0901
#define CL_DEVICE_NAME 0x102B
#define CL_DEVICE_VERSION 0x102F
#define CL_DRIVER_VERSION 0x102D
#define CL_DEVICE_GLOBAL_MEM_SIZE 0x101F
#define CL_MEM_READ_ONLY (1 << 2)
#define CL_MEM_WRITE_ONLY (1 << 1)
#define CL_MEM_COPY_HOST_PTR (1 << 5)
#define CL_TRUE 1

cl_int clGetPlatformIDs(cl_uint, cl_platform_id *, cl_uint *);
cl_int clGetPlatformInfo(cl_platform_id, cl_uint, size_t, void *, size_t *);
cl_int clGetDeviceIDs(cl_platform_id, cl_bitfield, cl_uint, cl_device_id *, cl_uint *);
cl_int clGetDeviceInfo(cl_device_id, cl_uint, size_t, void *, size_t *);
cl_context clCreateContext(void *, cl_uint, const cl_device_id *, void *, void *, cl_int *);
cl_command_queue clCreateCommandQueue(cl_context, cl_device_id, cl_bitfield, cl_int *);
cl_mem clCreateBuffer(cl_context, cl_bitfield, size_t, void *, cl_int *);
cl_program clCreateProgramWithSource(cl_context, cl_uint, const char **, const size_t *, cl_int *);
cl_int clBuildProgram(cl_program, cl_uint, const cl_device_id *, const char *, void *, void *);
cl_kernel clCreateKernel(cl_program, const char *, cl_int *);
cl_int clSetKernelArg(cl_kernel, cl_uint, size_t, const void *);
cl_int clEnqueueNDRangeKernel(cl_command_queue, cl_kernel, cl_uint, const size_t *, const size_t *,
			      const size_t *, cl_uint, void *, void *);
cl_int clEnqueueReadBuffer(cl_command_queue, cl_mem, cl_uint, size_t, size_t, void *, cl_uint, void *, void *);
cl_int clFinish(cl_command_queue);

static const char *src =
	"__kernel void add(__global float *a, __global float *b, __global float *c) {"
	"  size_t i = get_global_id(0); c[i] = a[i] * a[i] + b[i]; }";

static const char *burn_src =
	"__kernel void burn(__global float *a, __global float *c) {"
	"  float x = a[get_global_id(0)];"
	"  for (int k = 0; k < 48; k++) x = sqrt(x * x + 1.0f) * 0.5f + sin(x) * cos(x * 0.5f);"
	"  c[get_global_id(0)] = x; }";

static double now(void)
{
	struct timespec t;
	clock_gettime(1, &t);
	return t.tv_sec + t.tv_nsec / 1e9;
}

int main(int argc, char **argv, char **envp)
{
	cl_platform_id plat;
	cl_device_id dev;
	cl_int err;
	char s[256];
	cl_ulong mem;
	enum { N = 1 << 22 };

	if ((err = clGetPlatformIDs(1, &plat, NULL))) { printf("clGetPlatformIDs: %d\n", err); return 1; }
	clGetPlatformInfo(plat, CL_PLATFORM_NAME, sizeof(s), s, NULL); printf("platform: %s\n", s);
	clGetPlatformInfo(plat, CL_PLATFORM_VERSION, sizeof(s), s, NULL); printf("version:  %s\n", s);
	if ((err = clGetDeviceIDs(plat, CL_DEVICE_TYPE_GPU, 1, &dev, NULL))) { printf("clGetDeviceIDs: %d\n", err); return 1; }
	clGetDeviceInfo(dev, CL_DEVICE_NAME, sizeof(s), s, NULL); printf("device:   %s\n", s);
	clGetDeviceInfo(dev, CL_DEVICE_VERSION, sizeof(s), s, NULL); printf("device version: %s\n", s);
	clGetDeviceInfo(dev, CL_DRIVER_VERSION, sizeof(s), s, NULL); printf("driver:   %s\n", s);
	clGetDeviceInfo(dev, CL_DEVICE_GLOBAL_MEM_SIZE, sizeof(mem), &mem, NULL);
	printf("memory:   %llu MiB\n", (unsigned long long)(mem >> 20));

	cl_context ctx = clCreateContext(NULL, 1, &dev, NULL, NULL, &err);
	if (!ctx) { printf("clCreateContext: %d\n", err); return 1; }
	cl_command_queue q = clCreateCommandQueue(ctx, dev, 0, &err);
	float *a = malloc(N * sizeof(float)), *b = malloc(N * sizeof(float)), *c = malloc(N * sizeof(float));
	for (int i = 0; i < N; i++) { a[i] = i % 1000; b[i] = 1; }
	cl_mem ba = clCreateBuffer(ctx, CL_MEM_READ_ONLY | CL_MEM_COPY_HOST_PTR, N * sizeof(float), a, &err);
	cl_mem bb = clCreateBuffer(ctx, CL_MEM_READ_ONLY | CL_MEM_COPY_HOST_PTR, N * sizeof(float), b, &err);
	cl_mem bc = clCreateBuffer(ctx, CL_MEM_WRITE_ONLY, N * sizeof(float), NULL, &err);
	cl_program p = clCreateProgramWithSource(ctx, 1, &src, NULL, &err);
	if ((err = clBuildProgram(p, 1, &dev, "", NULL, NULL))) { printf("clBuildProgram: %d\n", err); return 1; }
	if (argc > 1 && !strcmp(argv[1], "loop")) {
		int secs = argc > 2 ? atoi(argv[2]) : 60;
		cl_program bp = clCreateProgramWithSource(ctx, 1, &burn_src, NULL, &err);
		if ((err = clBuildProgram(bp, 1, &dev, "", NULL, NULL))) { printf("clBuildProgram: %d\n", err); return 1; }
		cl_kernel bk = clCreateKernel(bp, "burn", &err);
		clSetKernelArg(bk, 0, sizeof(cl_mem), &ba);
		clSetKernelArg(bk, 1, sizeof(cl_mem), &bc);
		size_t n = N;
		double start = now(), mark = start;
		long runs = 0, total = 0;
		for (;;) {
			if ((err = clEnqueueNDRangeKernel(q, bk, 1, NULL, &n, NULL, 0, NULL, NULL))) { printf("enqueue: %d\n", err); return 1; }
			clFinish(q);
			runs++; total++;
			double t = now();
			if (t - mark >= 1.0) {
				printf("gpu stress: %.2f runs/s (%.1f M elements/s), %ld runs in %.0f s\n",
				       runs / (t - mark), runs * (double)N / 1e6 / (t - mark), total, t - start);
				fflush(NULL);
				runs = 0; mark = t;
			}
			if (secs > 0 && t - start >= secs) return 0;
		}
	}
	cl_kernel k = clCreateKernel(p, "add", &err);
	clSetKernelArg(k, 0, sizeof(cl_mem), &ba);
	clSetKernelArg(k, 1, sizeof(cl_mem), &bb);
	clSetKernelArg(k, 2, sizeof(cl_mem), &bc);
	size_t gs = N;
	double t0 = now();
	if ((err = clEnqueueNDRangeKernel(q, k, 1, NULL, &gs, NULL, 0, NULL, NULL))) { printf("enqueue: %d\n", err); return 1; }
	clFinish(q);
	double t1 = now();
	clEnqueueReadBuffer(q, bc, CL_TRUE, 0, N * sizeof(float), c, 0, NULL, NULL);
	int bad = 0;
	for (int i = 0; i < N; i++) if (c[i] != a[i] * a[i] + b[i]) bad++;
	double t2 = now();
	for (int i = 0; i < N; i++) c[i] = a[i] * a[i] + b[i];
	double t3 = now();
	printf("vector add of %d floats: GPU %.1f ms, CPU (1 core) %.1f ms, errors %d\n", N,
	       (t1 - t0) * 1e3, (t3 - t2) * 1e3, bad);
	return bad != 0;
}
