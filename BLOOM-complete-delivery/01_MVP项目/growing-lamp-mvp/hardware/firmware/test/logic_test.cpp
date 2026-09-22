#include "lamp_logic.h"
#include <cassert>
#include <iostream>
int main(){
 using namespace bloom;
 const char* id="0123456789abcdef0123456789abcdef";uint8_t bytes[NDEF_TAG_BYTES];char parsed[33];
 assert(validInstance(id));assert(!validInstance("invalid"));assert(makeNdef(id,bytes));assert(readNdef(bytes,parsed));assert(!strcmp(id,parsed));
 for(unsigned i: {0U,1U,2U,5U,9U,46U}){uint8_t original=bytes[i];bytes[i]^=0x20;assert(!readNdef(bytes,parsed));bytes[i]=original;}
 bytes[1]=0;assert(!readNdef(bytes,parsed)); // interrupted-write commit marker
 assert(std::abs(ntcCelsius(2047.5)-25)<.1f);assert(std::isnan(ntcCelsius(0)));assert(std::isnan(ntcCelsius(4095)));
 Safety safety;assert(!safety.permit());safety.sample(2047.5);assert(safety.permit());safety.sample(0);assert(!safety.permit());safety.sample(2047.5);assert(!safety.permit());assert(safety.clear());assert(safety.permit());
 safety.sample(650);assert(safety.latched);assert(!safety.clear());safety.sample(2047.5);assert(safety.clear());
 assert(boundedDuty(100,60)==153);assert(boundedDuty(-3)==0);assert(boundedDuty(35,35)==89);
 assert(elapsed(5,0xfffffff0U,20));assert(!elapsed(10,5,20));
 std::cout<<"PASS: NDEF round-trip, corruption, interrupted write, temperature, latched safety, PWM limits, millis wrap\n";
}
