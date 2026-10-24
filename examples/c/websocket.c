/* Independent consumer of the generated message ABI; no Python adapter. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "fastls.h"

static int wait_send(uint64_t id,int op,void *data,size_t size) {
    for(unsigned i=0;i<100;i++) {
        int code=fastls_ws_send(id,op,data,size);
        if(code==0)return 1;
        if(code!=1||fastls_wait(id,4,100)<0)return 0;
    }
    return 0;
}

static int receive(uint64_t id,int expected,const unsigned char *body,size_t total) {
    size_t received=0;
    for(unsigned i=0;i<100;i++) {
        unsigned char buffer[3];size_t count=0;int opcode=0,end=0;
        int code=fastls_ws_read(id,buffer,sizeof(buffer),&count,&opcode,&end);
        if(code==1){if(fastls_wait(id,2,100)<0)return 0;continue;}
        if(code!=0||opcode!=expected||received+count>total)return 0;
        if(count && memcmp(buffer,body+received,count))return 0;
        received+=count;
        if(end)return received==total;
    }
    return 0;
}

int main(int argc,char **argv) {
    if(argc!=2)return 2;
    char *end=NULL;unsigned long port=strtoul(argv[1],&end,10);
    if(*end||port==0||port>65535)return 2;
    char input[256];int size=snprintf(input,sizeof(input),"{\"url\":\"ws://127.0.0.1:%lu/ws\",\"profile\":\"chrome/156/windows\"}",port);
    if(size<1||(size_t)size>=sizeof(input))return 2;
    char *result=fastls_ws_start(input,(size_t)size);
    if(!result)return 3;
    char *field=strstr(result,"\"result\":");
    uint64_t id=field?(uint64_t)strtoull(field+9,NULL,10):0;
    fastls_free(result);if(!id)return 3;
    int ok=0;
    if(fastls_wait(id,1,3000)!=0)goto cleanup;
    unsigned char body[]={0,128,255,42};
    if(!wait_send(id,2,body,sizeof(body))||!receive(id,2,body,sizeof(body)))goto cleanup;
    if(!wait_send(id,1,NULL,0)||!receive(id,1,NULL,0))goto cleanup;
    unsigned char closing[]={3,232};
    if(!wait_send(id,8,closing,sizeof(closing)))goto cleanup;
    for(unsigned i=0;i<100;i++) {
        unsigned char buffer[3];size_t count;int opcode,finished;
        int code=fastls_ws_read(id,buffer,sizeof(buffer),&count,&opcode,&finished);
        if(code==2){ok=1;break;}
        if(code!=1||fastls_wait(id,2,100)<0)break;
    }
cleanup:
    result=fastls_close(id);fastls_free(result);
    if(!ok||fastls_ws_send(id,1,NULL,0)!=-2)return 4;
    puts("{\"ok\":true,\"binary_bytes\":4,\"empty_text\":true,\"close_handshake\":true}");
    return 0;
}
