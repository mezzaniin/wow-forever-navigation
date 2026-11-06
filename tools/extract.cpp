// extract <wowdir> <product> <outdir> < "fileid;path" lines
#include "CascLib/src/CascLib.h"  // aus tools/build.sh: work/CascLib
#include <cstdio>
#include <string>
#include <vector>
#include <iostream>
#include <sys/stat.h>
static void mkdirs(const std::string& p){ for(size_t i=1;i<p.size();i++) if(p[i]=='/'){ mkdir(p.substr(0,i).c_str(),0755);} }
int main(int argc,char**argv){
  if(argc<4){fprintf(stderr,"usage\n");return 1;}
  std::string spec=std::string(argv[1])+"*"+argv[2];
  HANDLE hs;
  if(!CascOpenStorage(spec.c_str(),0,&hs)){fprintf(stderr,"open storage failed: %d\n",GetCascError());return 2;}
  std::string line; int ok=0,fail=0;
  while(std::getline(std::cin,line)){
    auto sc=line.find(';'); if(sc==std::string::npos) continue;
    unsigned id=std::stoul(line.substr(0,sc)); std::string path=line.substr(sc+1);
    HANDLE hf;
    if(!CascOpenFile(hs,CASC_FILE_DATA_ID(id),0,CASC_OPEN_BY_FILEID,&hf)){fail++;continue;}
    std::vector<char> buf; char tmp[65536]; DWORD rd;
    while(CascReadFile(hf,tmp,sizeof tmp,&rd)&&rd>0) buf.insert(buf.end(),tmp,tmp+rd);
    CascCloseFile(hf);
    if(buf.empty()){fail++;continue;}
    std::string out=std::string(argv[3])+"/"+path; mkdirs(out);
    FILE*f=fopen(out.c_str(),"wb"); fwrite(buf.data(),1,buf.size(),f); fclose(f); ok++;
  }
  fprintf(stderr,"ok=%d fail=%d\n",ok,fail);
  CascCloseStorage(hs); return 0;
}
