#include <stdlib.h>
#include <stdio.h>
#include <string.h>

#define DATA_LIST_MAX_NUM 	32
#define APP_NAME_MAX_LEN 	64

struct boost_full_list {
	int 	level;
	char* 	data_list[DATA_LIST_MAX_NUM];
};

void xor_cipher(char* src,int src_len,char* key,int key_len)
{
	int done=0,i=0;
	while(done<src_len)
	{
		for(i=0;i<key_len;i++)
		{
			*(src+done)^=*(key+i);
		}
			done++;
		}
}

void xor_decipher(char* src,int src_len,char* key,int key_len)
{
	int done=0,i=0;
	while(done<src_len)
	{
		for(i=key_len-1;i>=0;i--)
		{
			*(src+done)^=*(key+i);
		}
		done++;
	}
}

void generate_cipher_data(FILE* fp,char* src,int src_len,char* key,int key_len)
{
	char buf[256];
	strcpy(buf, src);
	printf("%s -> ",src);
	xor_decipher(buf,src_len,key,key_len);
	printf("%s (%d)-> ",buf, strlen(buf));
	fputs(buf,fp);fputc('\n',fp);
	xor_decipher(buf,src_len,key,key_len);
	printf("%s\n",buf);
}

int main()
{
	int i=0, j=0;
	char cipher_key[]="mali";

	struct boost_full_list full_list[] =
	{
		//max:680M
		{
			.level = 10,
			.data_list =
			{
				"com.glbenchmark.glbenchmark",
				"net.kishonti.gfxbench.gl",
				"com.antutu.benchmark",
				"se.nena.nenamark",
				"com.rightware.Basemark",
				"com.aurorasoftworks.quadrant",
				"com.ludashi.benchmark"
			}
		},

		//next:600M
		{
			.level = 9,
			.data_list =
			{
				"/system/bin/surfaceflinger",
				"android.openglperf.cts"
			}
		},

		//256M
		{
			.level = 7,
			.data_list =
			{
			}
		},

		//153M
		{
			.level = 5,
			.data_list =
			{
			}
		},
	};
	int list_num = sizeof(full_list)/sizeof(full_list[0]);

	FILE* fp=NULL;
	fp=fopen("./libboost.so","wb");

	//list num
	char str_list_num[5] = {0};
	snprintf(str_list_num, 5, "%d", list_num);
	generate_cipher_data(fp,str_list_num,strlen(str_list_num),cipher_key,strlen(cipher_key));

	//data list max num
	char data_list_max_num[5] = {0};
	snprintf(data_list_max_num, 5, "%d", DATA_LIST_MAX_NUM);
	generate_cipher_data(fp,data_list_max_num,strlen(data_list_max_num),cipher_key,strlen(cipher_key));

	//app name max len
	char app_name_max_len[5] = {0};
	snprintf(app_name_max_len, 5, "%d", APP_NAME_MAX_LEN);
	generate_cipher_data(fp,app_name_max_len,strlen(app_name_max_len),cipher_key,strlen(cipher_key));

	for (i=0; i<list_num; i++)
	{
		//level
		char str_level[5] = {0};
		snprintf(str_level, 5, "%d", full_list[i].level);
		generate_cipher_data(fp,str_level,strlen(str_level),cipher_key,strlen(cipher_key));

		//app list
		for (j=0; j<DATA_LIST_MAX_NUM; j++)
		{
			if (NULL != full_list[i].data_list[j])
			{
				generate_cipher_data(fp,full_list[i].data_list[j],strlen(full_list[i].data_list[j]),cipher_key,strlen(cipher_key));
			}
			else
			{
				break;
			}
		}
		fputs("\n",fp);
	}

	fclose(fp);

	return 0;
}
