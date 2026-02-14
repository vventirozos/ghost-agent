import * as THREE from 'three';

let scene, camera, renderer, sphere, material;
let time = 0;
let spikeStrength = 0.2;
let targetSpikeStrength = 0.2;
let errorState = false;
let workingState = false;
let pulseState = 0;

// Vertex Shader
const vertexShader = `
uniform float uTime;
uniform float uSpikeStrength;
varying vec3 vNormal;
varying vec3 vPosition;
varying float vDisplacement;

// Simplex 3D Noise 
vec3 mod289(vec3 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 mod289(vec4 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 permute(vec4 x) { return mod289(((x*34.0)+1.0)*x); }
vec4 taylorInvSqrt(vec4 r) { return 1.79284291400159 - 0.85373472095314 * r; }
float snoise(vec3 v) {
    const vec2  C = vec2(1.0/6.0, 1.0/3.0) ;
    const vec4  D = vec4(0.0, 0.5, 1.0, 2.0);
    vec3 i  = floor(v + dot(v, C.yyy) );
    vec3 x0 = v - i + dot(i, C.xxx) ;
    vec3 g = step(x0.yzx, x0.xyz);
    vec3 l = 1.0 - g;
    vec3 i1 = min( g.xyz, l.zxy );
    vec3 i2 = max( g.xyz, l.zxy );
    vec3 x1 = x0 - i1 + C.xxx;
    vec3 x2 = x0 - i2 + C.yyy; 
    vec3 x3 = x0 - D.yyy;      
    i = mod289(i);
    vec4 p = permute( permute( permute(
                i.z + vec4(0.0, i1.z, i2.z, 1.0 ))
            + i.y + vec4(0.0, i1.y, i2.y, 1.0 ))
            + i.x + vec4(0.0, i1.x, i2.x, 1.0 ));
    float n_ = 0.142857142857; 
    vec3  ns = n_ * D.wyz - D.xzx;
    vec4 j = p - 49.0 * floor(p * ns.z * ns.z);  
    vec4 x_ = floor(j * ns.z);
    vec4 y_ = floor(j - 7.0 * x_ );    
    vec4 x = x_ *ns.x + ns.yyyy;
    vec4 y = y_ *ns.x + ns.yyyy;
    vec4 h = 1.0 - abs(x) - abs(y);
    vec4 b0 = vec4( x.xy, y.xy );
    vec4 b1 = vec4( x.zw, y.zw );
    vec4 s0 = floor(b0)*2.0 + 1.0;
    vec4 s1 = floor(b1)*2.0 + 1.0;
    vec4 sh = -step(h, vec4(0.0));
    vec4 a0 = b0.xzyw + s0.xzyw*sh.xxyy ;
    vec4 a1 = b1.xzyw + s1.xzyw*sh.zzww ;
    vec3 p0 = vec3(a0.xy,h.x);
    vec3 p1 = vec3(a0.zw,h.y);
    vec3 p2 = vec3(a1.xy,h.z);
    vec3 p3 = vec3(a1.zw,h.w);
    vec4 norm = taylorInvSqrt(vec4(dot(p0,p0), dot(p1,p1), dot(p2, p2), dot(p3,p3)));
    p0 *= norm.x;
    p1 *= norm.y;
    p2 *= norm.z;
    p3 *= norm.w;
    vec4 m = max(0.6 - vec4(dot(x0,x0), dot(x1,x1), dot(x2,x2), dot(x3,x3)), 0.0);
    m = m * m;
    return 42.0 * dot( m*m, vec4( dot(p0,x0), dot(p1,x1), dot(p2,x2), dot(p3,x3) ) );
}

void main() {
    vNormal = normal;
    vPosition = position;
    
    // Slower time scale for gentler movement
    float timeScale = uTime * 0.3; 
    if (uSpikeStrength > 0.5) timeScale = uTime * 1.0; // Faster when active, but still controlled

    float noise = snoise(position * 1.2 + timeScale);
    float noise2 = snoise(position * 2.5 - timeScale * 1.2) * 0.5;
    
    float displacement = (noise + noise2) * uSpikeStrength;
    vDisplacement = displacement;
    
    vec3 newPosition = position + normal * displacement;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(newPosition, 1.0);
}
`;

// Fragment Shader
const fragmentShader = `
uniform float uTime;
uniform bool uErrorState;
uniform bool uWorkingState;
uniform float uPulse;
uniform vec3 uFlashColor; // NEW: Dynamic Flash Color

varying vec3 vNormal;
varying vec3 vPosition;
varying float vDisplacement;

void main() {
    vec3 normal = normalize(vNormal);
    vec3 viewDir = normalize(cameraPosition - vPosition);
    float fresnel = pow(1.0 - dot(normal, vec3(0.0, 0.0, 1.0)), 2.0); 
    
    vec3 color;
    
    if (uErrorState) {
        float pulse = sin(uTime * 8.0) * 0.5 + 0.5;
        color = mix(vec3(0.5, 0.0, 0.0), vec3(1.0, 0.0, 0.2), pulse + vDisplacement);
        color += vec3(1.0, 0.2, 0.0) * fresnel * 2.0;
    } else {
        float t = uTime * 0.15; // Increased Speed (+10%)
        
        vec3 cPurple = vec3(0.12, 0.0, 0.2); 
        vec3 cBlue = vec3(0.0, 0.0, 0.2);    
        vec3 cGreen = vec3(0.0, 0.12, 0.0);   
        vec3 cGray = vec3(0.08, 0.08, 0.08);
        
        float cycle = mod(t, 4.0);
        vec3 baseColor;
        
        if (cycle < 1.0) baseColor = mix(cPurple, cBlue, cycle);
        else if (cycle < 2.0) baseColor = mix(cBlue, cGreen, cycle - 1.0);
        else if (cycle < 3.0) baseColor = mix(cGreen, cGray, cycle - 2.0);
        else baseColor = mix(cGray, cPurple, cycle - 3.0);
        
        float brightness = 1.0 + vDisplacement * 1.5;
        
        if (uWorkingState) {
            brightness += sin(uTime * 4.0) * 0.3;
            baseColor *= 1.3; 
        }

        color = baseColor * brightness;
        
        // Flash Effect REMOVED as per request.
        // The sphere no longer pulses light on logs.
        // Is kept reacting physically via Spikes for errors.

        color += baseColor * fresnel * 0.5;
        
        // Global Brightness Boost (+10%)
        color *= 1.1;
    }

    gl_FragColor = vec4(color, 1.0);
}
`;

export function initSphere() {
    const container = document.getElementById('sphere-container');

    scene = new THREE.Scene();
    camera = new THREE.PerspectiveCamera(75, container.clientWidth / container.clientHeight, 0.1, 1000);
    camera.position.z = 4.5;

    renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true, powerPreference: "high-performance" });
    renderer.setSize(container.clientWidth, container.clientHeight);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    container.appendChild(renderer.domElement);

    // Geometry: Reduced Scale 1.53 -> 1.3 (Another ~15% reduction)
    const geometry = new THREE.IcosahedronGeometry(1.3, 60);

    material = new THREE.ShaderMaterial({
        vertexShader: vertexShader,
        fragmentShader: fragmentShader,
        uniforms: {
            uTime: { value: 0 },
            uSpikeStrength: { value: 0.2 },
            uErrorState: { value: false },
            uWorkingState: { value: false },
            uPulse: { value: 0.0 },
            uFlashColor: { value: new THREE.Color(0x00ffff) } // Default Cyan
        },
        transparent: true
    });

    sphere = new THREE.Mesh(geometry, material);
    scene.add(sphere);

    window.addEventListener('resize', () => {
        const width = container.clientWidth;
        const height = container.clientHeight;
        renderer.setSize(width, height);
        camera.aspect = width / height;
        camera.updateProjectionMatrix();
    });

    animate();
}

function animate() {
    requestAnimationFrame(animate);

    time += 0.005; // Faster time step (+10%)

    material.uniforms.uTime.value = time;

    if (errorState) {
        targetSpikeStrength = 1.0;
    } else if (workingState) {
        // Working: Smoother, slower wave
        targetSpikeStrength = 0.6 + Math.sin(time * 2.0) * 0.1;
    } else {
        // Idle: Very low
        targetSpikeStrength = 0.15 + Math.sin(time * 0.5) * 0.05 + pulseState * 0.2;
    }

    if (pulseState > 0) {
        pulseState -= 0.02; // Slower decay
        if (pulseState < 0) pulseState = 0;
    }

    material.uniforms.uPulse.value = pulseState;

    // Smoother interpolation
    let lerpFactor = 0.02; // Slower transition
    if (workingState) lerpFactor = 0.03;
    if (errorState) lerpFactor = 0.1;

    spikeStrength += (targetSpikeStrength - spikeStrength) * lerpFactor;
    material.uniforms.uSpikeStrength.value = spikeStrength;
    material.uniforms.uErrorState.value = errorState;
    material.uniforms.uWorkingState.value = workingState;

    sphere.rotation.x += 0.0005;
    sphere.rotation.y += 0.001;
    if (workingState) {
        sphere.rotation.y += 0.005;
    }

    renderer.render(scene, camera);
}

export function updateSphereColor(colorHex) { }

export function triggerSpike() {
    errorState = true;
    setTimeout(() => { errorState = false; }, 2000);
}

export function triggerPulse(colorHex = '#00ffff') {
    pulseState = 1.0;
    if (material && material.uniforms.uFlashColor) {
        material.uniforms.uFlashColor.value.set(colorHex);
    }
}

export function setWorkingState(isWorking) {
    workingState = isWorking;
}
