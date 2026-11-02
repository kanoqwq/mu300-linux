#include <stdio.h>
#include "../../tools/ui/qr.h"
int main(int argc, char **argv)
{
	static QrCode q;
	int v = qr_encode(&q, argv[1], (int)strlen(argv[1]));
	if (!v) { puts("0"); return 1; }
	printf("%d\n", q.size);
	for (int y = 0; y < q.size; y++) { for (int x = 0; x < q.size; x++) putchar('0' + q.dark[y * q.size + x]); putchar('\n'); }
	return 0;
}
