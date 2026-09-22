"""Small, explicit glTF geometry fixtures; not Tripo-generated assets."""
import json
import struct


def encode(doc,blob):
    doc=json.loads(json.dumps(doc))
    if isinstance(doc,dict):
        doc.setdefault('asset',{'version':'2.0'})
        doc.setdefault('buffers',[{'byteLength':len(blob)}])
    encoded=json.dumps(doc).encode();encoded+=b' '*((-len(encoded))%4)
    blob+=b'\0'*((-len(blob))%4)
    return struct.pack('<III',0x46546c67,2,28+len(encoded)+len(blob))+struct.pack('<II',len(encoded),0x4e4f534a)+encoded+struct.pack('<II',len(blob),0x004e4942)+blob


def tetrahedron():
    points=[(0,0,0),(2,0,0),(0,1,0),(0,0,3)]
    faces=[0,2,1,0,1,3,0,3,2,1,2,3]
    blob=b''.join(struct.pack('<3f',*p) for p in points)+struct.pack('<12H',*faces)
    doc={'asset':{'version':'2.0'},'buffers':[{'byteLength':len(blob)}],
         'bufferViews':[{'buffer':0,'byteOffset':0,'byteLength':48},{'buffer':0,'byteOffset':48,'byteLength':24}],
         'accessors':[{'bufferView':0,'componentType':5126,'count':4,'type':'VEC3'},
                      {'bufferView':1,'componentType':5123,'count':12,'type':'SCALAR'}],
         'meshes':[{'primitives':[{'attributes':{'POSITION':0},'indices':1}]}],
         'nodes':[{'mesh':0}],'scenes':[{'nodes':[0]}],'scene':0}
    return doc,blob
