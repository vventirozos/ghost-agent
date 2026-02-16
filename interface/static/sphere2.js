import * as THREE from 'three';

let scene, camera, renderer, sphere, material;
let time = 0;

// Ferrofluid state variables
let spikeStrength = 0.05;
let targetSpikeStrength = 0.05;
let sharpness = 1.0;
let targetSharpness = 1.0;
let tremor = 0.0;
let targetTremor = 0.0;

let isWorking = 0.0;
let targetWorking = 0.0;
let isError = 0.0;
let targetError = 0.0;

const vertexShader = `
uniform float uTime;
uniform float uSpikeStrength;
uniform float uSharpness;
uniform float uTremor;
uniform float uWorking;

varying vec3 vNormal;
varying vec3 vViewPosition;
varying float vDisplacement;

// Simplex 3D Noise
vec3 mod289(vec3 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 mod289(vec4 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 permute(vec4 x) { return mod289(((x*34.0)+1.0)*x); }
vec4 taylorInvSqrt(vec4 r) { return 1.79284291400159 - 0.85373472095314 * r; }

float snoise(vec3 v) {
    const vec2  C = vec2(1.0/6.0, 1.0/3.0);
    const vec4  D = vec4(0.0, 0.5, 1.0, 2.0);
    vec3 i  = floor(v + dot(v, C.yyy));
    vec3 x0 = v - i + dot(i, C.xxx);
    vec3 g = step(x0.yzx, x0.xyz);
    vec3 l = 1.0 - g;
    vec3 i1 = min( g.xyz, l.zxy );
    vec3 i2 = max( g.xyz, l.zxy );
    vec3 x1 = x0 - i1 + C.xxx;
    vec3 x2 = x0 - i2 + C.yyy;
    vec3 x3 = x0 - D.yyy;
    i = mod289(i);
    vec4 p = permute( permute( permute( i.z + vec4(0.0, i1.z, i2.z, 1.0 )) + i.y + vec4(0.0, i1.y, i2.y, 1.0 )) + i.x + vec4(0.0, i1.x, i2.x, 1.0 ));
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
    p0 *= norm.x; p1 *= norm.y; p2 *= norm.z; p3 *= norm.w;
    vec4 m = max(0.6 - vec4(dot(x0,x0), dot(x1,x1), dot(x2,x2), dot(x3,x3)), 0.0);
    m = m * m;
    return 42.0 * dot( m*m, vec4( dot(p0,x0), dot(p1,x1), dot(p2,x2), dot(p3,x3) ) );
}

// FERROFLUID MATH: Ridge noise creates sharp, magnetic peaks
float getDisplacement(vec3 p) {
    float t = uTime * 0.4;
    
    // Smooth breathing base - reduce bumps on spikes when working
    float baseAmp = 0.1 * (1.0 - clamp(uWorking * 1.5, 0.0, 1.0)); // Zero out base noise quickly
    float baseNoise = snoise(p * 1.5 + t * 0.5) * baseAmp;
    
    // Sharp absolute noise (Ridges) for magnetic spikes
    // REVERTED to original frequency (2.5) as requested
    vec3 p1 = p * 2.5 + vec3(t * 1.2, -t * 0.8, t * 0.5);
    float noiseVal = snoise(p1);
    float ridge = 1.0 - abs(noiseVal);
    
    // The exponent dictates how "sharp" and distinct the spikes are
    ridge = pow(ridge, uSharpness);
    
    // High-frequency tremor when processing
    float tremorNoise = snoise(p * 8.0 + uTime * 6.0) * uTremor;
    
    // Combine
    float activeSpikes = ridge * uSpikeStrength;
    
    return baseNoise + activeSpikes + tremorNoise;
}

void main() {
    float d0 = getDisplacement(position);
    vDisplacement = d0;
    
    // Analytical Normals for perfectly smooth liquid light reflections on deformed geometry
    float eps = 0.01;
    vec3 tangent = normalize(cross(normal, vec3(0.0, 1.0, 0.0)));
    if (length(tangent) < 0.1) tangent = normalize(cross(normal, vec3(1.0, 0.0, 0.0)));
    vec3 bitangent = normalize(cross(normal, tangent));
    
    vec3 p1 = position + tangent * eps;
    vec3 p2 = position + bitangent * eps;
    
    vec3 pos0 = position + normal * d0;
    vec3 pos1 = p1 + normalize(p1) * getDisplacement(p1); 
    vec3 pos2 = p2 + normalize(p2) * getDisplacement(p2);
    
    vec3 computedNormal = normalize(cross(pos1 - pos0, pos2 - pos0));
    vNormal = normalMatrix * computedNormal;
    
    vec4 mvPosition = modelViewMatrix * vec4(pos0, 1.0);
    vViewPosition = -mvPosition.xyz;
    gl_Position = projectionMatrix * mvPosition;
}
`;

const fragmentShader = `
uniform float uTime;
uniform float uError;
uniform float uWorking;

varying vec3 vNormal;
varying vec3 vViewPosition;
varying float vDisplacement;

void main() {
    vec3 normal = normalize(vNormal);
    vec3 viewDir = normalize(vViewPosition);
    
    // Virtual Studio Lighting Setup
    vec3 keyLightDir = normalize(vec3(1.0, 2.0, 1.0));
    vec3 fillLightDir = normalize(vec3(-1.0, -0.5, 0.5));
    
    // Base Ferrofluid Color: Very dark grey/black metallic
    vec3 baseColor = vec3(0.02, 0.02, 0.03); 
    
    // 1. Glossy Specular (Wet Liquid Metal Look)
    vec3 halfKey = normalize(keyLightDir + viewDir);
    float specKey = pow(max(dot(normal, halfKey), 0.0), 120.0); // Extremely sharp white hot highlight
    
    vec3 halfFill = normalize(fillLightDir + viewDir);
    float specFill = pow(max(dot(normal, halfFill), 0.0), 30.0); // Softer reflection
    
    // 2. Fresnel Rim (Edge lighting, environment reflection)
    float fresnel = pow(1.0 - max(dot(normal, viewDir), 0.0), 4.0);
    
    // 3. Eerie Magnetic Iridescence / Underglow
    // Cycle deep colors: Red -> Green -> Blue based on time
    vec3 deepRed = vec3(0.4, 0.0, 0.0);
    vec3 deepGreen = vec3(0.0, 0.4, 0.0);
    vec3 deepBlue = vec3(0.0, 0.0, 0.5); // Slightly brighter blue for visibility
    
    float cycle = uTime * 0.5; // Slow cycle speed
    vec3 busyColor = mix(deepRed, deepGreen, 0.5 + 0.5 * sin(cycle));
    busyColor = mix(busyColor, deepBlue, 0.5 + 0.5 * cos(cycle * 0.7));
    
    // Mix with error state (Bright Red)
    vec3 activeColor = mix(busyColor, vec3(1.0, 0.0, 0.0), uError);
    vec3 rimColor = mix(vec3(0.1, 0.1, 0.15), activeColor, uWorking + uError);

    // Composite the liquid metal
    vec3 finalColor = baseColor;
    finalColor += vec3(1.0) * specKey * 1.5;         // Key reflection (white hot)
    finalColor += vec3(0.3, 0.4, 0.5) * specFill * 0.4;        // Fill reflection (cool metal)
    finalColor += rimColor * fresnel * 1.5;                    // Atmospheric rim

    // Enhance the pure white pin-prick highlights on the absolute sharpest peaks
    float peakMask = smoothstep(0.1, 0.3, vDisplacement);
    finalColor += vec3(1.0) * peakMask * specKey * 2.0;
    
    // Core glow when highly active
    finalColor += activeColor * vDisplacement * (uWorking + uError) * 1.2;

    gl_FragColor = vec4(finalColor, 1.0);
}
`;

export function initSphere() {
    const container = document.getElementById('sphere-container');
    scene = new THREE.Scene();

    camera = new THREE.PerspectiveCamera(45, container.clientWidth / container.clientHeight, 0.1, 1000);
    camera.position.z = 4.2;

    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "high-performance" });
    renderer.setSize(container.clientWidth, container.clientHeight);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    container.appendChild(renderer.domElement);

    // Ultra-High fidelity geometry so the liquid peaks don't look blocky
    const geometry = new THREE.IcosahedronGeometry(1.2, 180);

    material = new THREE.ShaderMaterial({
        vertexShader, fragmentShader,
        uniforms: {
            uTime: { value: 0 },
            uSpikeStrength: { value: 0.05 },
            uSharpness: { value: 1.0 },
            uTremor: { value: 0.0 },
            uWorking: { value: 0.0 },
            uError: { value: 0.0 }
        }
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

    time += 0.01;

    // Physics / State machine for the Ferrofluid
    if (targetError > 0.5) {
        targetSpikeStrength = 0.5;
        targetSharpness = 8.0;
        targetTremor = 0.03;
    } else if (targetWorking > 0.5) {
        // High magnetic pull
        targetSpikeStrength = 0.4 + Math.sin(time * 6.0) * 0.05;
        targetSharpness = 3.0;
        targetTremor = 0.0; // REMOVED tremor to stop jitter
    } else {
        // Relaxed, bubbling liquid blob
        targetSpikeStrength = 0.05 + Math.sin(time * 2.0) * 0.02;
        targetSharpness = 1.0;
        targetTremor = 0.0;
    }

    // Organic interpolation (spring physics simulation)
    spikeStrength += (targetSpikeStrength - spikeStrength) * 0.06;
    sharpness += (targetSharpness - sharpness) * 0.06;
    tremor += (targetTremor - tremor) * 0.1;

    isWorking += (targetWorking - isWorking) * 0.08;
    isError += (targetError - isError) * 0.1;

    material.uniforms.uTime.value = time;
    material.uniforms.uSpikeStrength.value = spikeStrength;
    material.uniforms.uSharpness.value = sharpness;
    material.uniforms.uTremor.value = tremor;
    material.uniforms.uWorking.value = isWorking;
    material.uniforms.uError.value = isError;

    // Gentle levitation sway
    sphere.position.y = Math.sin(time * 1.5) * 0.08;

    // Rotation based on activity
    sphere.rotation.x += isWorking > 0.5 ? 0.003 : 0.001;
    sphere.rotation.y += isWorking > 0.5 ? 0.005 : 0.002;

    renderer.render(scene, camera);
}

export function setWorkingState(working) { targetWorking = working ? 1.0 : 0.0; }
export function triggerSpike() { targetError = 1.0; setTimeout(() => { targetError = 0.0; }, 1000); }
export function triggerPulse() { spikeStrength += 0.2; } // Quick magnetic spike
