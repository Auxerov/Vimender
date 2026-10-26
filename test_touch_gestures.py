import sys, types, math, importlib
from unittest import mock


class Vector:
    def __init__(s, v=(0,0,0)): s.v = [float(x) for x in v]
    x=property(lambda s:s.v[0]); y=property(lambda s:s.v[1]); z=property(lambda s:s.v[2])
    def __add__(s,o): return Vector([a+b for a,b in zip(s.v,o.v)])
    def __sub__(s,o): return Vector([a-b for a,b in zip(s.v,o.v)])
    def __mul__(s,k): return Vector([a*k for a in s.v])
    __rmul__=__mul__
    def __neg__(s): return Vector([-a for a in s.v])
    def dot(s,o): return sum(a*b for a,b in zip(s.v,o.v))
    def __getitem__(s,i): return s.v[i]
    def __matmul__(s,o): return NotImplemented
class Quaternion:
    def __init__(s, a=(1,0,0,0), ang=None):
        if ang is not None:
            ax = Vector(a); n = math.sqrt(ax.dot(ax)); ax = ax*(1/n)
            h = ang/2; sn = math.sin(h)
            s.q = (math.cos(h), ax.x*sn, ax.y*sn, ax.z*sn)
        else: s.q = tuple(a)
    def __matmul__(s,o):
        if isinstance(o, Vector):
            w,x,y,z = s.q; qv = Quaternion((0,o.x,o.y,o.z))
            r = s @ qv @ Quaternion((w,-x,-y,-z)); return Vector(r.q[1:])
        w1,x1,y1,z1 = s.q; w2,x2,y2,z2 = o.q
        return Quaternion((w1*w2-x1*x2-y1*y2-z1*z2, w1*x2+x1*w2+y1*z2-z1*y2,
                           w1*y2-x1*z2+y1*w2+z1*x2, w1*z2+x1*y2-y1*x2+z1*w2))
    def normalized(s):
        n = math.sqrt(sum(c*c for c in s.q)); return Quaternion(tuple(c/n for c in s.q))
mu = types.ModuleType('mathutils'); mu.Vector=Vector; mu.Quaternion=Quaternion; mu.Matrix=object
sys.modules['mathutils']=mu


for m in ['bpy','blf','bmesh','gpu','bpy_extras','bpy_extras.view3d_utils']:
    sys.modules[m]=mock.MagicMock()
sys.modules['bpy_extras'].view3d_utils = sys.modules['bpy_extras.view3d_utils']


ev = types.ModuleType('evdev'); ec = types.SimpleNamespace(
    EV_ABS=3, EV_SYN=0, SYN_REPORT=0, ABS_MT_SLOT=0x2f, ABS_MT_TRACKING_ID=0x39,
    ABS_MT_POSITION_X=0x35, ABS_MT_POSITION_Y=0x36, ABS_MT_TOOL_TYPE=0x37)
ev.ecodes = ec; sys.modules['evdev']=ev; sys.modules['evdev.ecodes']=ec

import os
pkg = types.ModuleType('vim'); pkg.__path__=[os.path.dirname(os.path.abspath(__file__))]; sys.modules['vim']=pkg
st = importlib.import_module('vim.state'); mt = importlib.import_module('vim.mode_tablet')
S = st.S
T = mt.EvdevMultiTouchTracker()
T.x_rng = T.y_rng = 1000.0
S.tablet_in_transform = None; S.tablet_active_axis=None; S.tablet_select_tool='TWEAK'

def frame(*pts, t=None):
    T.slots.clear()
    for i,(x,y) in enumerate(pts): T.slots[i]={'x':x,'y':y,'down':True,'palm':False}
    T._process_frame()
def lift(): T.slots.clear(); T._process_frame()
def take():
    with T.lock:
        r = dict(orbit=tuple(T.orbit_delta), pan=tuple(T.pan_delta), zoom=T.zoom_log, twist=T.twist)
        T.orbit_delta=[0.0,0.0]; T.pan_delta=[0.0,0.0]; T.zoom_log=0.0; T.twist=0.0
    return r

ok = lambda c, m: print(("PASS " if c else "FAIL ")+m)


frame((500,500)); frame((501,500)); r=take(); ok(r['orbit']==(0.0,0.0),"1-finger jitter under slop ignored")
for x in range(510,600,10): frame((x,500))
r=take(); ok(r['orbit'][0]>0.05 and abs(r['orbit'][1])<1e-9,"1-finger drag right => +x orbit delta")
lift()

frame((400,500),(600,500)); take()
for k in range(1,12): frame((400-k*10,500),(600+k*10,500))
r=take(); ok(r['zoom']>0.3,"pinch spread => positive zoom_log (zoom in)"); ok(abs(r['twist'])<1e-6,"pinch has no twist"); ok(abs(r['pan'][0])<1e-6,"symmetric pinch has no pan")
lift()

frame((400,500),(600,500)); take()
for k in range(1,12): frame((400+k*10,500),(600+k*10,500))
r=take(); ok(r['pan'][0]>0.05 and abs(r['zoom'])<1e-6 and abs(r['twist'])<1e-6,"2-finger pan right => +x pan only")
lift()

frame((400,500),(600,500)); take()
for k in range(1,16):
    a=math.radians(k*3); cx,cy,r_=500,500,100
    frame((cx-r_*math.cos(a),cy-r_*math.sin(a)),(cx+r_*math.cos(a),cy+r_*math.sin(a)))
r=take(); ok(r['twist']>0.3,f"clockwise twist => positive twist ({r['twist']:.2f} rad)"); ok(abs(r['zoom'])<1e-6,"twist has no zoom")
lift()

T.slots.clear(); T.slots[0]={'x':100,'y':100,'down':True,'palm':False}; T.slots[1]={'x':900,'y':900,'down':True,'palm':True}; T._process_frame()
ok(T.finger_count==1,"palm contact not counted")
lift()

import time as _t
frame((400,500),(600,500)); lift(); ok(T.pending_undo,"2-finger tap => undo"); T.pending_undo=False
frame((400,500),(500,500),(600,500)); lift(); ok(T.pending_redo,"3-finger tap => redo")

T.pending_undo=False
frame((400,500),(600,500))
for k in range(1,12): frame((400-k*10,500),(600+k*10,500))
lift(); ok(not T.pending_undo,"pinch is not a tap")


class RV: pass
rv=RV(); rv.view_rotation=Quaternion((1,0,0,0)); rv.view_location=Vector((0,0,0)); rv.view_distance=10.0
sp=types.SimpleNamespace(lens=50.0); reg=types.SimpleNamespace(width=1000,height=600)
N=mt._TouchNavigator()
u0 = 10.0*36.0/(50.0*1000)

N._step(rv,sp,reg,0,0,0,100.0,0.0,0.0,(0.0,0.0))
ok(abs(rv.view_location.x - (-100*u0))<1e-9,"pan 100px right moves view_location -100*u (content follows fingers)")

rv.view_location=Vector((0,0,0)); rv.view_distance=10.0
sx,sy=300.0,100.0
zlog=math.log(2.0)
N._step(rv,sp,reg,0,0,0,0.0,0.0,zlog,(sx,sy))
d=rv.view_distance; u1=d*36.0/(50.0*1000)

P=Vector((sx*u0, sy*u0, 0)); off=(P-rv.view_location)*(1/u1)
ok(abs(d-5.0)<1e-9,"zoom x2 halves distance"); ok(abs(off.x-sx)<1e-6 and abs(off.y-sy)<1e-6,"point under fingers stays under fingers after pinch")

rv.view_rotation=Quaternion((math.cos(0.5*math.radians(70)),math.sin(0.5*math.radians(70)),0,0)).normalized()
rv.view_location=Vector((0,0,0)); rv.view_distance=10
N._step(rv,sp,reg,0.2,0.0,0,0,0,0,(0,0))
up=rv.view_rotation@Vector((0,1,0)); ok(up.z>0.3,"turntable yaw keeps view upright")
rv.view_rotation=Quaternion((0,0,0,1));
N._step(rv,sp,reg,0.2,0.1,0,0,0,0,(0,0)); ok(True,"trackball branch runs when rolled")

rv.view_rotation=Quaternion((1,0,0,0)); N._step(rv,sp,reg,0,0,0.5,0,0,0,(0,0))
ok(abs((rv.view_rotation@Vector((0,1,0))).x - (-math.sin(0.5)))<1e-6,"roll +0.5 rad rotates view up vector CCW about view axis")
