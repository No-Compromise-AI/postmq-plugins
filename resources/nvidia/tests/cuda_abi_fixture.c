/* CPU-only ABI fixture. These are typed CUDA signatures, not a CUDA driver. */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
_Static_assert(sizeof(void *) == 8, "64-bit fixture required");
typedef struct { unsigned char bytes[16]; } CUuuid;
static void *host_memory;
static int current, retained, freed;
static const uint64_t device_pointer = UINT64_C(0x1000000042);
void fixture_reset(void) { free(host_memory); host_memory=NULL; current=retained=freed=0; }
int fixture_cleanup_count(void) { return freed; }
int cuInit(unsigned int flags) { return flags ? 1 : 0; }
int cuDriverGetVersion(int *version) { *version=13030; return 0; }
int cuDeviceGetCount(int *count) { *count=1; return 0; }
int cuDeviceGet(int *device, int ordinal) { *device=0; return ordinal ? 1 : 0; }
int cuDeviceGetUuid_v2(CUuuid *out, int device) {
    const unsigned char bytes[16]={0xaa,0xaa,0xaa,0xaa,0xaa,0xaa,0x4a,0xaa,0x8a,0xaa,0x00,0x00,0x00,0x00,0x00,0x07};
    memcpy(out->bytes,bytes,16); return device ? 1 : 0;
}
int cuDeviceGetName(char *name, int length, int device) { if(length<12||device)return 1; strcpy(name,"NVIDIA GB10"); return 0; }
int cuDeviceGetAttribute(int *value, int attribute, int device) { *value=1; return attribute!=18||device; }
int cuDevicePrimaryCtxRetain(void **context, int device) { retained=1; *context=(void *)(uintptr_t)UINT64_C(0x1000000011); return device; }
int cuCtxSetCurrent(void *context) { if(context&&(uintptr_t)context!=UINT64_C(0x1000000011))return 999; current=context!=NULL; return 0; }
int cuCtxSynchronize(void) { return current ? 0 : 999; }
int cuDevicePrimaryCtxRelease_v2(int device) { if(current||!retained||device)return 999; retained=0; freed++; return 0; }
int cuMemGetInfo_v2(size_t *free_bytes, size_t *total) { if(!current)return 999; *free_bytes=UINT64_C(4294967296); *total=UINT64_C(8589934592); return 0; }
int cuMemAlloc_v2(uint64_t *pointer, size_t bytes) { if(!current||bytes>1024)return 999; *pointer=device_pointer; return 0; }
int cuMemsetD8_v2(uint64_t pointer, unsigned char value, size_t bytes) { return !current||pointer!=device_pointer||value!=0xa5||bytes>1024 ? 999 : 0; }
int cuMemFree_v2(uint64_t pointer) { if(!current||pointer!=device_pointer)return 999; freed++; return 0; }
int cuMemHostAlloc(void **pointer, size_t bytes, unsigned int flags) { if(!current||bytes>1024||flags)return 999; host_memory=malloc(bytes); *pointer=host_memory; return host_memory ? 0 : 2; }
int cuMemFreeHost(void *pointer) { if(!current||pointer!=host_memory)return 999; free(pointer); host_memory=NULL; freed++; return 0; }
