#pragma once
#include <cmath>
#include <cstdint>
#include <cstring>
#include <algorithm>

namespace bloom {
constexpr unsigned TAG_BYTES=48;
inline bool validInstance(const char* id){
 if(!id||std::strlen(id)!=32)return false;
 for(unsigned i=0;i<32;i++)if(!((id[i]>='0'&&id[i]<='9')||(id[i]>='a'&&id[i]<='f')))return false;
 return true;
}
// NFC Forum Text RTD, UTF-8, language "en", application value BLM1:<32 hex>.
// Exactly 48 bytes starting at NTAG213 user page 4. Never write lock/config pages.
inline bool makeNdef(const char* instance,uint8_t out[TAG_BYTES]){
 if(!validInstance(instance))return false;
 std::memset(out,0,TAG_BYTES);out[0]=0x03;out[1]=44;out[2]=0xD1;out[3]=1;out[4]=40;out[5]='T';out[6]=2;out[7]='e';out[8]='n';
 std::memcpy(out+9,"BLM1:",5);std::memcpy(out+14,instance,32);out[46]=0xFE;return true;
}
inline bool readNdef(const uint8_t in[TAG_BYTES],char instance[33]){
 if(in[0]!=3||in[1]!=44||in[2]!=0xD1||in[3]!=1||in[4]!=40||in[5]!='T'||in[6]!=2||in[7]!='e'||in[8]!='n'||in[46]!=0xFE||std::memcmp(in+9,"BLM1:",5))return false;
 std::memcpy(instance,in+14,32);instance[32]=0;return validInstance(instance);
}
inline float ntcCelsius(float adc,float maximum=4095.0f){
 if(adc<=10||adc>=maximum-10)return NAN;
 const float r=10000.0f*adc/(maximum-adc);
 return 1.0f/(1.0f/298.15f+std::log(r/10000.0f)/3950.0f)-273.15f;
}
class Safety {
 public:
 bool latched=false;bool valid=false;float temperature=NAN;
 void sample(float adc){temperature=ntcCelsius(adc);valid=std::isfinite(temperature)&&temperature>=-10&&temperature<=85;
  if(!valid||temperature>=55)latched=true;}
 bool clear(){if(valid&&temperature<45){latched=false;return true;}return false;}
 bool permit()const{return valid&&!latched;}
};
inline uint8_t boundedDuty(int percent,int ceiling=60){return static_cast<uint8_t>(std::max(0,std::min(percent,ceiling))*255/100);}
inline bool elapsed(uint32_t now,uint32_t previous,uint32_t interval){return static_cast<uint32_t>(now-previous)>=interval;}
}
