"""360도 투영 셰이더 (GLSL 3.30 core).

입력 텍스처는 mpv가 원본 해상도로 그려 넣은 프레임이다. 화면의 각 픽셀마다 시선 방향을
계산하고, 그 방향이 원본(등장방형 또는 유튜브 EAC 큐브맵)의 어느 좌표에 해당하는지 구해
표본화한다. 확대 시 뭉개짐을 줄이기 위해 Catmull-Rom 바이큐빅(9탭) 표본화를 쓴다.
"""

VERT_SRC = """#version 330 core
out vec2 v_uv;
void main() {
    vec2 p = vec2((gl_VertexID == 1) ? 3.0 : -1.0, (gl_VertexID == 2) ? 3.0 : -1.0);
    v_uv = p * 0.5 + 0.5;
    gl_Position = vec4(p, 0.0, 1.0);
}
"""

FRAG_SRC = """#version 330 core
in vec2 v_uv;
out vec4 fragColor;

uniform sampler2D u_tex;
uniform vec2  u_texSize;     // 텍스처 크기(픽셀)
uniform vec2  u_viewSize;    // 출력 크기(픽셀)
uniform float u_yaw;         // 라디안, + = 오른쪽
uniform float u_pitch;       // 라디안, + = 위
uniform float u_fov;         // 가로 시야각(라디안)
uniform int   u_projection;  // 0 = 등장방형, 1 = 유튜브 EAC, 2 = 듀얼 피쉬아이(Insta360 원본)
uniform int   u_fisheyeSwap; // 1 = 앞/뒤 렌즈 좌우 바꿈
// 듀얼 피쉬아이 보정 (vr/stitch.py 와 같은 계산). 0 = 앞 렌즈, 1 = 뒤 렌즈
uniform vec2  u_lensC0, u_lensC1;      // 칸 안 원 중심 (0..1)
uniform float u_lensR0, u_lensR1;      // 칸 폭 대비 원 반지름
uniform float u_lensFov0, u_lensFov1;  // 렌즈 화각(라디안)
uniform mat3  u_globalRot;             // 수평·방향 보정
uniform mat3  u_backRot;               // 뒤 렌즈 틀어짐 보정
uniform float u_blend;                 // 이음새를 섞는 폭(라디안, ±)
uniform int   u_stereo;      // 0 = 모노, 1 = 상하 분할, 2 = 좌우 분할
uniform int   u_hq;          // 1 = 바이큐빅 표본화
uniform int   u_flipY;       // 1 = 텍스처 세로 뒤집기

const float PI = 3.14159265358979;

vec4 fetch(vec2 uv) { return texture(u_tex, uv); }

// Catmull-Rom 바이큐빅, 바이리니어 9탭 최적화 버전
vec4 sampleBicubic(vec2 uv) {
    vec2 ts = u_texSize;
    vec2 coord = uv * ts - 0.5;
    vec2 f = fract(coord);
    coord -= f;
    vec2 w0 = f * (-0.5 + f * (1.0 - 0.5 * f));
    vec2 w1 = 1.0 + f * f * (-2.5 + 1.5 * f);
    vec2 w2 = f * (0.5 + f * (2.0 - 1.5 * f));
    vec2 w3 = f * f * (-0.5 + 0.5 * f);
    vec2 w12 = w1 + w2;
    vec2 off12 = w2 / w12;
    vec2 tc0  = (coord - 0.5) / ts;
    vec2 tc3  = (coord + 2.5) / ts;
    vec2 tc12 = (coord + off12 + 0.5) / ts;
    vec4 c = vec4(0.0);
    c += fetch(vec2(tc0.x,  tc0.y))  * w0.x  * w0.y;
    c += fetch(vec2(tc12.x, tc0.y))  * w12.x * w0.y;
    c += fetch(vec2(tc3.x,  tc0.y))  * w3.x  * w0.y;
    c += fetch(vec2(tc0.x,  tc12.y)) * w0.x  * w12.y;
    c += fetch(vec2(tc12.x, tc12.y)) * w12.x * w12.y;
    c += fetch(vec2(tc3.x,  tc12.y)) * w3.x  * w12.y;
    c += fetch(vec2(tc0.x,  tc3.y))  * w0.x  * w3.y;
    c += fetch(vec2(tc12.x, tc3.y))  * w12.x * w3.y;
    c += fetch(vec2(tc3.x,  tc3.y))  * w3.x  * w3.y;
    return c;
}

// 화면 픽셀 → 시선 방향 (x 오른쪽, y 위, z 뒤; 정면은 -z)
vec3 rayDir() {
    float aspect = u_viewSize.x / u_viewSize.y;
    float tanH = tan(u_fov * 0.5);
    vec2 ndc = v_uv * 2.0 - 1.0;
    vec3 d = normalize(vec3(ndc.x * tanH, ndc.y * tanH / aspect, -1.0));
    float cp = cos(u_pitch), sp = sin(u_pitch);
    d = vec3(d.x, cp * d.y - sp * d.z, sp * d.y + cp * d.z);
    float cy = cos(u_yaw), sy = sin(u_yaw);
    d = vec3(cy * d.x - sy * d.z, d.y, sy * d.x + cy * d.z);
    return d;
}

vec2 equirectUV(vec3 d) {
    float lon = atan(d.x, -d.z);
    float lat = asin(clamp(d.y, -1.0, 1.0));
    return vec2(lon / (2.0 * PI) + 0.5, 0.5 - lat / PI);
}

// 유튜브 EAC (3x2 큐브맵, FFmpeg v360 의 eac 입력 규약과 동일)
vec2 eacUV(vec3 d) {
    // FFmpeg 규약: x 오른쪽, y 아래, z 정면
    vec3 v = vec3(d.x, -d.y, -d.z);
    float phi = atan(v.x, v.z);
    float theta = asin(clamp(v.y, -1.0, 1.0));
    int dir; float phiN;
    if (phi >= -PI / 4.0 && phi < PI / 4.0)                 { dir = 4; phiN = phi; }               // FRONT
    else if (phi >= -(PI / 2.0 + PI / 4.0) && phi < -PI / 4.0) { dir = 1; phiN = phi + PI / 2.0; } // LEFT
    else if (phi >= PI / 4.0 && phi < PI / 2.0 + PI / 4.0)     { dir = 0; phiN = phi - PI / 2.0; } // RIGHT
    else { dir = 5; phiN = phi + ((phi > 0.0) ? -PI : PI); }                                      // BACK
    float thr = atan(cos(phiN));
    if (theta > thr) dir = 3;        // DOWN
    else if (theta < -thr) dir = 2;  // UP

    float uf, vf;
    if (dir == 0)      { uf = -v.z / v.x; vf =  v.y / v.x; }   // RIGHT
    else if (dir == 1) { uf = -v.z / v.x; vf = -v.y / v.x; }   // LEFT
    else if (dir == 2) { uf = -v.x / v.y; vf = -v.z / v.y; }   // UP
    else if (dir == 3) { uf =  v.x / v.y; vf = -v.z / v.y; }   // DOWN
    else if (dir == 4) { uf =  v.x / v.z; vf =  v.y / v.z; }   // FRONT
    else               { uf =  v.x / v.z; vf = -v.y / v.z; }   // BACK

    int face; int rot;
    if (dir == 0)      { face = 2; rot = 0;   }
    else if (dir == 1) { face = 0; rot = 0;   }
    else if (dir == 2) { face = 5; rot = 270; }
    else if (dir == 3) { face = 3; rot = 270; }
    else if (dir == 4) { face = 1; rot = 0;   }
    else               { face = 4; rot = 90;  }
    // 아래 줄 세 면은 90도 돌아가 있다 (FFmpeg rotate_cube_face 와 같은 방향)
    float t;
    if (rot == 90)       { t = uf; uf = -vf; vf =  t; }
    else if (rot == 180) { uf = -uf; vf = -vf; }
    else if (rot == 270) { t = uf; uf =  vf; vf = -t; }

    uf = (2.0 / PI) * atan(uf) + 0.5;
    vf = (2.0 / PI) * atan(vf) + 0.5;
    // 면 경계의 블리딩을 줄이기 위해 아주 조금 안쪽으로
    uf = clamp(uf, 0.001, 0.999);
    vf = clamp(vf, 0.001, 0.999);
    float uFace = float(face - (face / 3) * 3);
    float vFace = float(face / 3);
    return vec2((uf + uFace) / 3.0, (vf + vFace) / 2.0);
}

vec4 sampleAt(vec2 uv) {
    if (u_stereo == 1)      uv.y *= 0.5;
    else if (u_stereo == 2) uv.x *= 0.5;
    if (u_flipY == 1) uv.y = 1.0 - uv.y;
    return (u_hq == 1) ? sampleBicubic(uv) : texture(u_tex, uv);
}

// 렌즈 로컬 방향 l → 텍스처 좌표 (등거리 투영). phi = 광축에서 벗어난 각
vec2 lensUV(vec3 l, vec2 c, float r, float fov, float tile, out float phi) {
    phi = acos(clamp(l.z, -1.0, 1.0));
    float theta = atan(l.y, l.x);
    float rr = r * phi / (fov * 0.5);
    vec2 t = c + rr * vec2(cos(theta), sin(theta));
    return vec2((tile + t.x) * 0.5, t.y);
}

// 듀얼 피쉬아이: 두 렌즈를 보정값대로 투영하고, 경계 띠에서는 부드럽게 섞는다
vec4 dfisheyeColor(vec3 d) {
    vec3 c = u_globalRot * d;
    vec3 v = vec3(c.x, -c.y, -c.z);                 // x 오른쪽, y 아래, z 앞 렌즈 광축
    float tileF = (u_fisheyeSwap == 1) ? 0.0 : 1.0; // 기본: 오른쪽 칸 = 앞 렌즈
    float pf, pb;
    vec2 uf = lensUV(v, u_lensC0, u_lensR0, u_lensFov0, tileF, pf);
    vec2 ub = lensUV(u_backRot * vec3(-v.x, v.y, -v.z), u_lensC1, u_lensR1, u_lensFov1, 1.0 - tileF, pb);
    float w = smoothstep(-max(u_blend, 1e-4), max(u_blend, 1e-4), pb - pf);   // 1 = 앞 렌즈
    if (pf > u_lensFov0 * 0.5) w = 0.0;
    if (pb > u_lensFov1 * 0.5) w = 1.0;
    if (w >= 0.999) return sampleAt(uf);
    if (w <= 0.001) return sampleAt(ub);
    return mix(sampleAt(ub), sampleAt(uf), w);
}

void main() {
    vec3 d = rayDir();
    if (u_projection == 2) {
        fragColor = dfisheyeColor(d);
    } else {
        fragColor = sampleAt((u_projection == 1) ? eacUV(d) : equirectUV(d));
    }
    fragColor.a = 1.0;
}
"""
