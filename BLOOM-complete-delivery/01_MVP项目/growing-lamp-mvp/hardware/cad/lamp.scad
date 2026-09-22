/* BLOOM / growing lamp / mechanical prototype v1
   Units: mm. Editable in OpenSCAD. All dimensions are design assumptions,
   not measurements of a built sample. Print fit_coupon BEFORE complete parts.
   part options are exported by tools/export_cad.py.
*/
$fn = 96;
part = "assembly";
exploded = 0;
clearance = 0.5;           // radial crown clearance: bore76 vs pilot75
stem_od = 12;
stem_id = 8;
stem_length = 174;
nfc_board_w = 43;         // adjust to the measured PN532 module
nfc_board_h = 41;

module screw_hole(h=50,d=3.4){cylinder(d=d,h=h,center=true);}
module screw_positions(r=35){for(a=[90,210,330])rotate([0,0,a])translate([r,0,0])children();}
module rounded_box(s,r=2){linear_extrude(height=s[2])offset(r=r)offset(delta=-r)square([s[0],s[1]],center=true);}
module base_shell(){
 difference(){
  union(){
   difference(){cylinder(d=140,h=34);translate([0,0,-1])cylinder(d=133,h=31);}
   for(a=[45,135,225,315])rotate([0,0,a])translate([63,0,0])cylinder(d=10,h=31);
   // External split clamp, accessible from above; avoid overtightening in resin.
   translate([0,0,34])cylinder(d=26,h=20);
   translate([0,10,34])rounded_box([30,10,20],2);
  }
  translate([0,0,24])cylinder(d=stem_od+0.4,h=35);
  translate([-.65,0,33])cube([1.3,20,23]);
  translate([0,11,44])rotate([0,90,0])screw_hole(40);
  for(a=[45,135,225,315])rotate([0,0,a])translate([63,0,12])screw_hole(35,2.7);
  // rear cable exit, grommet required; not a bare mains connector
  translate([0,67,15])rotate([90,0,0])cylinder(d=9,h=12,center=true);
  // panel-mounted button, final bore must match actual purchased switch
  translate([34,-23,28])cylinder(d=8.2,h=10);
  // ventilation slots, on rear half
  for(x=[-35,-21,21,35])translate([x,28,29])rounded_box([4,15,8],1.5);
 }
}
module base_lid(){
 difference(){
  union(){cylinder(d=132.5,h=3);for(a=[45,135,225,315])rotate([0,0,a])translate([63,0,0])cylinder(d=10,h=3);}
  for(a=[45,135,225,315])rotate([0,0,a])translate([63,0,1.5])screw_hole(8,3.4);
  // zip-tie slots for electronics; no assumed commercial PCB hole pattern
  for(x=[-25,25],y=[-16,16])translate([x,y,1.5])cube([3,10,7],center=true);
 }
}
module head_core(){
 difference(){
  union(){
   difference(){translate([0,0,-28])cylinder(d=96,h=28);translate([0,0,-25])cylinder(d=88,h=22.01);}
   // annular pilot / face seat; crown rests at z=0
   difference(){cylinder(d=75,h=4);translate([0,0,-1])cylinder(d=56,h=7);}
   screw_positions()translate([0,0,-25])cylinder(d=9,h=29);
   // NFC non-metallic wing, antenna normal towards crown underside
   translate([0,-55,-10])rounded_box([nfc_board_w+6,nfc_board_h+6,3],2);
   for(x=[-(nfc_board_w+4)/2,(nfc_board_w+4)/2])translate([x,-55,-7])rounded_box([2,nfc_board_h+6,5],.5);
  }
  translate([0,0,-3.1])cylinder(d=56,h=8);
  screw_positions()translate([0,0,-11])screw_hole(60,3.4);
  // rear cable entry and head-bracket fasteners
  translate([0,-25,-27])cylinder(d=9,h=10,center=true);
  for(x=[-18,18])translate([x,-32,-27])screw_hole(10,3.4);
  // PN532 zip ties; keep metal away from antenna. Board sits on nylon spacers.
  for(x=[-17,17],y=[-70,-40])translate([x,y,-8])cube([3,7,8],center=true);
  // main light board tie holes on rear plate (module-dependent placement)
  for(x=[-16,16],y=[-12,12])translate([x,y,-27])screw_hole(8,2.6);
 }
}
module retainer(){
 difference(){cylinder(d=86,h=3);translate([0,0,-1])cylinder(d=56,h=5);screw_positions()translate([0,0,1.5])screw_hole(8,3.4);}
}
module head_socket(){
 difference(){
  union(){
   translate([0,-32,-31])rounded_box([46,32,3],3);
   translate([0,-45,-52])rounded_box([25,42,24],3);
  }
  translate([0,-45,-40])rotate([90,0,0])cylinder(d=stem_od+.4,h=65,center=true);
  translate([0,-49,-48])screw_hole(24,3.0);
  for(x=[-18,18])translate([x,-32,-30])screw_hole(12,3.4);
  translate([0,-25,-36])rotate([90,0,0])cylinder(d=8,h=14,center=true);
 }
}
module leaf_button(){
 union(){linear_extrude(2.4)hull(){translate([-9,0])scale([1.3,.7])circle(8);translate([12,3])circle(1.8);}translate([-2,0,-2])cylinder(d=7,h=3);}
}
module fit_coupon(){
 // Three holes for checking printer-dependent fit before full production.
 difference(){rounded_box([112,42,4],3);for(i=[0:2])translate([-36+i*36,0,-1])cylinder(d=stem_od+[.2,.4,.6][i],h=6);}
 for(i=[0:2])translate([-36+i*36,-17,4])linear_extrude(.6)text(str(stem_od+[.2,.4,.6][i]),size=4,halign="center");
}
module crown_coupon(){
 difference(){cylinder(d=92,h=4);translate([0,0,-1])cylinder(d=76,h=6);translate([-60,0,-1])cube([120,60,6]);}
}
module gobo_body(){
 difference(){
  cylinder(d=42,h=55);
  translate([0,0,3])cylinder(d=32.4,h=60);
  translate([0,0,-1])cylinder(d=18,h=7);
  // card slot at z25: open to +Y, side rails retain tube integrity
  translate([-15.4,-16.8,24.1])cube([30.8,42,1.8]);
  for(a=[13,133,253])rotate([0,0,a])translate([19,0,45])rotate([0,90,0])cylinder(d=2.8,h=12,center=true);
  for(x=[-13,13])translate([x,0,1])screw_hole(10,2.6);
 }
}
module lens_holder(){
 difference(){
  union(){cylinder(d=32,h=25);translate([0,0,22])cylinder(d=37,h=3);}
  translate([0,0,-1])cylinder(d=23,h=30);
  translate([0,0,21])cylinder(d=25.4,h=5);
 }
}
module lens_retainer(){difference(){cylinder(d=29.5,h=1.5);translate([0,0,-1])cylinder(d=23,h=4);}}
module tree_2d(){
 hull(){translate([0,-13])circle(1.1);translate([0,10])circle(.7);}
 for(s=[-1,1],y=[-4,3,8]){
  hull(){translate([0,y-4])circle(.75);translate([s*(10-y*.3),y+3])circle(.65);}
  translate([s*(7-y*.15),y+2])rotate(s*30)scale([1.5,.65])circle(2.0);
 }
}
module gobo_card(kind="stars"){
 linear_extrude(1.2)difference(){
  union(){square([30,34],center=true);translate([0,21])square([12,10],center=true);}
  if(kind=="stars")for(i=[0:17])let(a=i*137.508,r=2+sqrt(i/17)*9)translate([r*cos(a),r*sin(a)])circle(r=.48+(i%3)*.19,$fn=16);
  if(kind=="forest")difference(){circle(r=11.5);tree_2d();}
 }
}
module projector_cradle(){
 difference(){
  union(){rounded_box([54,54,4],3);translate([0,0,12])rotate([60,0,0])difference(){cylinder(d=48,h=18,center=true);cylinder(d=42.6,h=24,center=true);translate([-35,0,-20])cube([70,40,40]);}}
  for(x=[-19,19],y=[-19,19])translate([x,y,2])screw_hole(10,3.4);
 }
}
module assembly(){
 color([.24,.28,.21])base_shell();
 color([.18,.21,.16])translate([0,0,-exploded*.5])base_lid();
 color([.55,.54,.40])translate([0,0,40])difference(){cylinder(d=stem_od,h=stem_length);cylinder(d=stem_id,h=stem_length+1);}
 translate([0,-40,250])rotate([90,0,0]){
  color([.30,.35,.26])head_core();color([.24,.28,.21])head_socket();
  color([.67,.73,.40])translate([0,0,exploded])import("../exports/crown_sunflower.stl",convexity=10);
  color([.66,.63,.43])translate([0,0,4+exploded*2])retainer();
  color([1,.88,.58,.8])translate([0,0,6+exploded*2])cylinder(d=55.5,h=1.5);
 }
 color([.44,.57,.30])translate([34,-23,38])leaf_button();
 // Projection pod is a separate cabled accessory, not inside the base.
 translate([105,40,0]){color([.20,.23,.19])projector_cradle();translate([0,0,30])rotate([60,0,0]){color([.23,.27,.23])gobo_body();color([.44,.48,.36])translate([0,0,40])lens_holder();color([.1,.12,.1])translate([0,0,25])gobo_card("forest");}}
}
if(part=="base_shell")base_shell();else if(part=="base_lid")base_lid();else if(part=="head_core")head_core();else if(part=="retainer")retainer();else if(part=="head_socket")head_socket();else if(part=="leaf_button")leaf_button();else if(part=="fit_coupon")fit_coupon();else if(part=="crown_coupon")crown_coupon();else if(part=="gobo_body")gobo_body();else if(part=="lens_holder")lens_holder();else if(part=="lens_retainer")lens_retainer();else if(part=="gobo_forest")gobo_card("forest");else if(part=="gobo_stars")gobo_card("stars");else if(part=="projector_cradle")projector_cradle();else assembly();
