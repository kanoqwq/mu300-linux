/* Minimal bionic start files (replaces the NDK's crtbegin_dynamic.o/crtend_android.o). */
typedef struct {
	void (**preinit_array)(void);
	void (**init_array)(void);
	void (**fini_array)(void);
} structors_array_t;

extern void __libc_init(void *raw_args, void (*onexit)(void), int (*slingshot)(int, char **, char **),
			structors_array_t const *structors) __attribute__((noreturn));
extern int main(int, char **, char **);
extern void (*__preinit_array_start[])(void) __attribute__((weak));
extern void (*__init_array_start[])(void) __attribute__((weak));
extern void (*__fini_array_start[])(void) __attribute__((weak));
void *__dso_handle = &__dso_handle;

__attribute__((used)) static void _start_main(void *raw_args)
{
	structors_array_t array = { __preinit_array_start, __init_array_start, __fini_array_start };
	__libc_init(raw_args, 0, &main, &array);
}

__asm__(".globl _start\n_start:\n  mov x0, sp\n  b _start_main\n");
