/* BLOOM firmware: local controls and NFC on the main loop; network on a separate
   FreeRTOS task. This is a module-level prototype, not a certified lamp controller.
   Full board compilation / RF / thermal validation must be performed by the builder.
*/
#include <Arduino.h>
#include <algorithm>
#include <cstring>
#include <cmath>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <SPI.h>
#include <Preferences.h>
#include <ArduinoJson.h>
#include <Adafruit_PN532.h>
#include <time.h>
#include "lamp_logic.h"
#if __has_include("config.local.h")
#include "config.local.h"
#else
#include "config.example.h"
#endif

constexpr uint8_t MAIN_LED=25,PROJ_LED=26,BUTTON=33,TEMP_ADC=34,NFC_SS=27;
constexpr uint8_t MAIN_CHANNEL=0,PROJ_CHANNEL=1;
constexpr size_t OUTBOX_MAX=32;
Adafruit_PN532 nfc(NFC_SS,&SPI);
bloom::Safety safety;
Preferences prefs;
QueueHandle_t commandQueue,eventQueue,ackQueue,cacheQueue;
portMUX_TYPE stateMux=portMUX_INITIALIZER_UNLOCKED;
struct State {bool power=true;int brightness=45;int projection=0;char theme[12]="sunflower";char instance[33]="";float temp=0;bool fault=true;};
State state;
struct Command {char id[40];bool bind;bool power;int brightness;int projection;char theme[12];char instance[33];uint32_t ttlMs;};
struct Message {char json[384];};
struct CacheMessage {char json[4096];};
struct Tag {char instance[33];char theme[12];};
Tag known[16];size_t knownCount=0;
char processed[16][40]{};size_t processedIndex=0;
Command pendingBind{};bool hasBind=false,bindConfirmed=false,nfcReady=false;
uint32_t bindStarted=0,lastNfcSeen=0,lastNfcPoll=0,lastTemperature=0,lastFade=0,lastPersist=0,projectionStarted=0;
String lastTag;String bootId;uint32_t eventCounter=0;
uint8_t actualMain=0,actualProj=0;
bool rawButton=false,stableButton=false,longAction=false;uint32_t buttonChanged=0,buttonStarted=0,lastDim=0;

State snapshot(){portENTER_CRITICAL(&stateMux);State s=state;portEXIT_CRITICAL(&stateMux);return s;}
void replaceState(const State &s){portENTER_CRITICAL(&stateMux);state=s;portEXIT_CRITICAL(&stateMux);}
void event(const char* kind,const JsonDocument& payload){
 StaticJsonDocument<384> doc;doc["id"]=bootId+"-"+String(++eventCounter);doc["kind"]=kind;doc["payload"].set(payload.as<JsonVariantConst>());
 Message msg{};serializeJson(doc,msg.json,sizeof(msg.json));
 if(xQueueSend(eventQueue,&msg,0)!=pdTRUE){Message discarded;xQueueReceive(eventQueue,&discarded,0);xQueueSend(eventQueue,&msg,0);Serial.println("Outbox full: oldest event dropped; not a complete audit log");}
}
void simpleEvent(const char* kind,const char* detail){StaticJsonDocument<192> p;p["detail"]=detail;event(kind,p);}
void ack(const char* id,bool ok,const char* error=""){
 StaticJsonDocument<256> p;p["id"]=id;p["ok"]=ok;if(!ok)p["error"]=error;
 Message msg{};serializeJson(p,msg.json,sizeof(msg.json));xQueueSend(ackQueue,&msg,0);
}
bool alreadyProcessed(const char* id){for(auto &entry:processed)if(!strcmp(entry,id))return true;return false;}
void rememberCommand(const char* id){strlcpy(processed[processedIndex++%16],id,40);}
bool validTheme(const char* theme){return !strcmp(theme,"sunflower")||!strcmp(theme,"forest")||!strcmp(theme,"stars");}
void loadCache(const char* json){
 StaticJsonDocument<4096> doc;if(deserializeJson(doc,json))return;
 knownCount=0;
 for(JsonObject t:doc.as<JsonArray>()){
  const char* instance=t["instance_id"]|"";const char* theme=t["theme"]|"sunflower";
  if(knownCount>=16)break;if(!bloom::validInstance(instance)||!validTheme(theme))continue;
  strlcpy(known[knownCount].instance,instance,33);strlcpy(known[knownCount].theme,theme,12);knownCount++;
 }
}
const Tag* findTag(const char* instance){for(size_t i=0;i<knownCount;i++)if(!strcmp(known[i].instance,instance))return &known[i];return nullptr;}
void persistState(){
 State s=snapshot();StaticJsonDocument<256> p;p["power"]=s.power;p["brightness"]=s.brightness;p["theme"]=s.theme;p["instance"]=s.instance;
 String value;serializeJson(p,value);if(prefs.getString("state","")!=value)prefs.putString("state",value);
 // Projection deliberately never resumes automatically after reboot.
}
void restoreState(){
 String saved=prefs.getString("state","");StaticJsonDocument<256> p;if(deserializeJson(p,saved))return;
 State s;s.power=p["power"]|true;s.brightness=constrain(p["brightness"]|45,0,MAIN_PWM_MAX_PERCENT);
 const char* th=p["theme"]|"sunflower";if(validTheme(th))strlcpy(s.theme,th,12);
 const char* id=p["instance"]|"";if(bloom::validInstance(id))strlcpy(s.instance,id,33);s.projection=0;replaceState(s);
}
void installTag(const char* instance){
 const Tag* tag=findTag(instance);if(!tag){simpleEvent("unknown_tag","Valid BLOOM tag is not bound to this account/cache");return;}
 State s=snapshot();strlcpy(s.instance,instance,33);strlcpy(s.theme,tag->theme,12);s.power=true;
 // Presets change brightness, not the physical gobo. Projection remains manually controlled.
 s.brightness=!strcmp(tag->theme,"stars")?30:45;replaceState(s);persistState();
 StaticJsonDocument<192> p;p["instance_id"]=instance;event("outfit_installed",p);
}
void sampleSafety(uint32_t now);

bool readApplicationTag(char instance[33]){
 uint8_t bytes[bloom::TAG_BYTES]{};
 for(uint8_t page=4;page<16;page++){sampleSafety(millis());uint8_t buf[4];if(!nfc.ntag2xx_ReadPage(page,buf))return false;memcpy(bytes+(page-4)*4,buf,4);}
 return bloom::readNdef(bytes,instance);
}
bool writeApplicationTag(const char* instance){
 uint8_t cc[4];if(!nfc.ntag2xx_ReadPage(3,cc)||cc[0]!=0xE1||cc[2]*8<bloom::TAG_BYTES||(cc[3]&0x0f)!=0)return false;
 uint8_t bytes[bloom::TAG_BYTES];if(!bloom::makeNdef(instance,bytes))return false;
 // Commit TLV length last: an interrupted write must not be mistaken for a valid tag.
 uint8_t empty[4]={3,0,0,0};if(!nfc.ntag2xx_WritePage(4,empty))return false;
 for(uint8_t page=5;page<16;page++){sampleSafety(millis());if(!nfc.ntag2xx_WritePage(page,bytes+(page-4)*4))return false;}
 if(!nfc.ntag2xx_WritePage(4,bytes))return false;
 char verify[33];return readApplicationTag(verify)&&!strcmp(verify,instance);
}
void pollNfc(uint32_t now){
 if(!nfcReady||!bloom::elapsed(now,lastNfcPoll,180))return;lastNfcPoll=now;
 uint8_t uid[7],length=0;
 if(!nfc.readPassiveTargetID(PN532_MIFARE_ISO14443A,uid,&length,35)){
  if(bloom::elapsed(now,lastNfcSeen,1200))lastTag="";return;
 }
 lastNfcSeen=now;char hex[15]{};for(uint8_t i=0;i<length&&i<7;i++)snprintf(hex+i*2,3,"%02X",uid[i]);
 if(hasBind&&bindConfirmed){
  bool ok=writeApplicationTag(pendingBind.instance);
  if(ok){
   StaticJsonDocument<256> p;p["instance_id"]=pendingBind.instance;p["tag_uid"]=hex;p["verified"]=true;event("tag_bound",p);
   ack(pendingBind.id,true);rememberCommand(pendingBind.id);lastTag="";
  }else{ack(pendingBind.id,false,"NFC write/readback failed; use an unlocked NTAG213 tag");simpleEvent("tag_write_failed","Write/readback or capability-container validation failed");}
  hasBind=false;bindConfirmed=false;return;
 }
 if(lastTag==hex)return;lastTag=hex;char instance[33];
 if(readApplicationTag(instance))installTag(instance);else simpleEvent("unknown_tag","Tag does not contain a valid BLOOM NDEF record");
}
void processCommands(uint32_t now){
 Command c;
 while(xQueueReceive(commandQueue,&c,0)==pdTRUE){
  if(alreadyProcessed(c.id)){ack(c.id,true);continue;}
  if(c.bind){
   if(hasBind&&!strcmp(pendingBind.id,c.id))continue;
   if(hasBind){ack(c.id,false,"Another binding is pending");continue;}
   if(!nfcReady){ack(c.id,false,"PN532 not detected");continue;}
   if(!bloom::validInstance(c.instance)){ack(c.id,false,"Invalid instance ID");continue;}
   pendingBind=c;hasBind=true;bindConfirmed=false;bindStarted=now;Serial.println("Binding pending: present tag and short press button within 90 seconds");
  }else{
   if(!validTheme(c.theme)){ack(c.id,false,"Invalid theme");continue;}
   State s=snapshot();s.power=c.power;s.brightness=constrain(c.brightness,0,MAIN_PWM_MAX_PERCENT);s.projection=constrain(c.projection,0,PROJECTION_PWM_MAX_PERCENT);strlcpy(s.theme,c.theme,12);
   if(s.projection>0&&snapshot().projection==0)projectionStarted=now;
   replaceState(s);rememberCommand(c.id);ack(c.id,true);persistState();
  }
 }
 if(hasBind&&bloom::elapsed(now,bindStarted,pendingBind.ttlMs)){ack(pendingBind.id,false,"Local confirmation/tag timeout");hasBind=false;bindConfirmed=false;}
 CacheMessage cache;
 if(xQueueReceive(cacheQueue,&cache,0)==pdTRUE){loadCache(cache.json);String current(cache.json);if(prefs.getString("tags","")!=current)prefs.putString("tags",current);}
}
void handleButton(uint32_t now){
 bool raw=digitalRead(BUTTON)==LOW;
 if(raw!=rawButton){rawButton=raw;buttonChanged=now;}
 if(raw!=stableButton&&bloom::elapsed(now,buttonChanged,35)){
  stableButton=raw;
  if(stableButton){buttonStarted=now;lastDim=now;longAction=false;}
  else if(!longAction){
   if(hasBind){bindConfirmed=true;Serial.println("Physical write confirmation accepted");}
   else{State s=snapshot();s.power=!s.power;replaceState(s);persistState();simpleEvent("button","toggle");}
  }
 }
 if(stableButton&&bloom::elapsed(now,buttonStarted,600)){
  longAction=true;
  if(safety.latched){
   if(bloom::elapsed(now,buttonStarted,5000)&&safety.clear()){State s=snapshot();s.fault=false;s.power=false;s.projection=0;replaceState(s);simpleEvent("button","safety reset; lamp remains off");}
  }else if(bloom::elapsed(now,lastDim,250)){lastDim=now;State s=snapshot();s.brightness=s.brightness>=MAIN_PWM_MAX_PERCENT?15:s.brightness+5;replaceState(s);}
 }
}
void sampleSafety(uint32_t now){
 if(!bloom::elapsed(now,lastTemperature,200))return;lastTemperature=now;
 uint32_t mv=0;for(int i=0;i<8;i++)mv+=analogReadMilliVolts(TEMP_ADC);float average=mv/8.0f;
 bool previous=safety.latched;
 if(average<80||average>3100){safety.sample(0);}else safety.sample(average/NTC_SUPPLY_MV*4095.0f);
 State s=snapshot();s.temp=safety.temperature;s.fault=!safety.permit();if(s.fault){s.projection=0;}
 replaceState(s);
 if(!previous&&safety.latched)simpleEvent("safety_trip",safety.valid?"temperature >=55C; latched":"NTC open/short/out-of-range; latched");
 if(s.fault){actualMain=actualProj=0;ledcWrite(MAIN_CHANNEL,0);ledcWrite(PROJ_CHANNEL,0);}
}
void updateLight(uint32_t now){
 State s=snapshot();
 if(s.projection&&bloom::elapsed(now,projectionStarted,PROJECTION_TIMEOUT_MS)){s.projection=0;replaceState(s);simpleEvent("projection_timeout","15-minute prototype timeout");}
 if(!bloom::elapsed(now,lastFade,12))return;lastFade=now;
 int targetMain=s.power&&safety.permit()?bloom::boundedDuty(s.brightness,MAIN_PWM_MAX_PERCENT):0;
 int targetProj=s.power&&safety.permit()?bloom::boundedDuty(s.projection,PROJECTION_PWM_MAX_PERCENT):0;
 auto approach=[](uint8_t current,int target)->uint8_t{return current<target?current+1:current>target?current-1:current;};
 actualMain=approach(actualMain,targetMain);actualProj=approach(actualProj,targetProj);ledcWrite(MAIN_CHANNEL,actualMain);ledcWrite(PROJ_CHANNEL,actualProj);
}
void networkTask(void*){
 String pendingEvents[OUTBOX_MAX],pendingAcks[OUTBOX_MAX];size_t eventCount=0,ackCount=0;uint32_t lastWifi=0;bool timeConfigured=false;
 for(;;){
  if(WiFi.status()!=WL_CONNECTED){if(bloom::elapsed(millis(),lastWifi,10000)){lastWifi=millis();WiFi.reconnect();}vTaskDelay(pdMS_TO_TICKS(500));continue;}
  if(!timeConfigured){configTime(0,0,"pool.ntp.org");timeConfigured=true;}
  Message m;while(eventCount<OUTBOX_MAX&&xQueueReceive(eventQueue,&m,0)==pdTRUE)pendingEvents[eventCount++]=m.json;
  while(ackCount<OUTBOX_MAX&&xQueueReceive(ackQueue,&m,0)==pdTRUE)pendingAcks[ackCount++]=m.json;
  DynamicJsonDocument request(16384);State s=snapshot();JsonObject reported=request.createNestedObject("reported");
  reported["power"]=s.power&&!s.fault;reported["brightness"]=s.fault?0:s.brightness;reported["projection"]=s.fault?0:s.projection;
  reported["theme"]=s.theme;if(s.instance[0])reported["instance_id"]=s.instance;else reported["instance_id"]=nullptr;
  if(std::isfinite(s.temp))reported["temperature_c"]=s.temp;else reported["temperature_c"]=nullptr;
  reported["fault"]=s.fault;reported["firmware"]="bloom-esp32-1.0.0";reported["rssi"]=WiFi.RSSI();
  JsonArray events=request.createNestedArray("events"),acks=request.createNestedArray("acks");
  // Server accepts batches of at most 16; retained remainder is sent next cycle.
  size_t sendEvents=std::min<size_t>(16,eventCount),sendAcks=std::min<size_t>(16,ackCount);
  for(size_t i=0;i<sendEvents;i++){StaticJsonDocument<384> p;deserializeJson(p,pendingEvents[i]);events.add(p.as<JsonVariantConst>());}
  for(size_t i=0;i<sendAcks;i++){StaticJsonDocument<384> p;deserializeJson(p,pendingAcks[i]);acks.add(p.as<JsonVariantConst>());}
  String body;serializeJson(request,body);HTTPClient http;WiFiClient plain;WiFiClientSecure secure;String endpoint=String(SERVER_URL)+"/api/device/poll";bool began=false;
  if(endpoint.startsWith("https://")&&strlen(TLS_ROOT_CA)>0){secure.setCACert(TLS_ROOT_CA);began=http.begin(secure,endpoint);}
  else if(ALLOW_PLAINTEXT_LAN&&endpoint.startsWith("http://"))began=http.begin(plain,endpoint);
  if(!began){Serial.println("Invalid server transport/CA; local controls remain available");vTaskDelay(pdMS_TO_TICKS(2000));continue;}
  http.setConnectTimeout(1200);http.setTimeout(1600);http.addHeader("Content-Type","application/json");http.addHeader("Authorization",String("Bearer ")+DEVICE_TOKEN);
  int code=http.POST(body);
  if(code==200){
   DynamicJsonDocument response(16384);DeserializationError error=deserializeJson(response,http.getString());
   if(!error){
    for(size_t i=sendEvents;i<eventCount;i++)pendingEvents[i-sendEvents]=pendingEvents[i];eventCount-=sendEvents;
    for(size_t i=sendAcks;i<ackCount;i++)pendingAcks[i-sendAcks]=pendingAcks[i];ackCount-=sendAcks;
    CacheMessage cache{};serializeJson(response["known_tags"],cache.json,sizeof(cache.json));xQueueOverwrite(cacheQueue,&cache);
    uint32_t serverTime=response["server_time"]|0UL;
    for(JsonObject item:response["commands"].as<JsonArray>()){
     Command c{};strlcpy(c.id,item["id"]|"",sizeof(c.id));c.bind=strcmp(item["kind"]|"","bind")==0;
     JsonObject p=item["payload"];c.power=p["power"]|true;c.brightness=p["brightness"]|45;c.projection=p["projection"]|0;
     strlcpy(c.theme,p["theme"]|"sunflower",sizeof(c.theme));strlcpy(c.instance,p["instance_id"]|"",sizeof(c.instance));
     uint32_t expires=item["expires"]|serverTime;c.ttlMs=expires>serverTime?std::min<uint32_t>(90000,(expires-serverTime)*1000):0;
     if(c.ttlMs>0)xQueueSend(commandQueue,&c,0);
    }
   }
  }else{Serial.printf("Poll failed HTTP %d; retaining pending event/ack batch\n",code);}
  http.end();vTaskDelay(pdMS_TO_TICKS(1800));
 }
}
void setup(){
 pinMode(MAIN_LED,OUTPUT);pinMode(PROJ_LED,OUTPUT);digitalWrite(MAIN_LED,LOW);digitalWrite(PROJ_LED,LOW);pinMode(BUTTON,INPUT_PULLUP);
 Serial.begin(115200);ledcSetup(MAIN_CHANNEL,5000,8);ledcSetup(PROJ_CHANNEL,5000,8);ledcAttachPin(MAIN_LED,MAIN_CHANNEL);ledcAttachPin(PROJ_LED,PROJ_CHANNEL);ledcWrite(MAIN_CHANNEL,0);ledcWrite(PROJ_CHANNEL,0);
 analogReadResolution(12);analogSetPinAttenuation(TEMP_ADC,ADC_11db);
 commandQueue=xQueueCreate(12,sizeof(Command));eventQueue=xQueueCreate(OUTBOX_MAX,sizeof(Message));ackQueue=xQueueCreate(OUTBOX_MAX,sizeof(Message));cacheQueue=xQueueCreate(1,sizeof(CacheMessage));
 if(!commandQueue||!eventQueue||!ackQueue||!cacheQueue){Serial.println("Queue allocation failed; outputs remain off");while(true)delay(1000);}
 bootId=String((uint32_t)(ESP.getEfuseMac()>>16),HEX)+"-"+String(esp_random(),HEX);
 prefs.begin("bloom",false);restoreState();loadCache(prefs.getString("tags","[]").c_str());
 SPI.begin(18,19,23,NFC_SS);nfc.begin();nfcReady=nfc.getFirmwareVersion()!=0;
 if(nfcReady){nfc.SAMConfig();nfc.setPassiveActivationRetries(1);}else Serial.println("PN532 not detected: light/local safety still active");
 WiFi.mode(WIFI_STA);WiFi.setAutoReconnect(true);WiFi.begin(WIFI_SSID,WIFI_PASSWORD);
 xTaskCreatePinnedToCore(networkTask,"bloom-network",12288,nullptr,1,nullptr,0);simpleEvent("boot","ESP32 restarted; projection remains off");
}
void loop(){uint32_t now=millis();sampleSafety(now);handleButton(now);processCommands(now);pollNfc(now);updateLight(now);if(bloom::elapsed(now,lastPersist,10000)){lastPersist=now;persistState();}delay(3);}
