MODALITY_PRESETS = {
    "CR": {
        "label": "Computed Radiography",
        "description": "2D X-ray — phosphor plate detector",
        "color": "#79c0ff",
        "icon": "🫁",
        "sopClass": "1.2.840.10008.5.1.4.1.1.1",
        "groups": [
            {
                "name": "Anatomy",
                "fields": [
                    {"id": "bodyPart",    "label": "Body Part",     "type": "select",
                     "options": ["CHEST","HAND","KNEE","FOOT","PELVIS","SPINE","SKULL","SHOULDER","ELBOW","WRIST","HIP","ANKLE"],
                     "default": "CHEST"},
                    {"id": "viewPosition","label": "View Position", "type": "select",
                     "options": ["PA","AP","LAT","OBLIQUE"],         "default": "PA"},
                ]
            },
            {
                "name": "Image",
                "fields": [
                    {"id": "rows",        "label": "Rows",          "type": "number", "min": 512, "max": 4096, "step": 1,    "default": 2048, "unit": "px"},
                    {"id": "cols",        "label": "Columns",       "type": "number", "min": 512, "max": 4096, "step": 1,    "default": 2048, "unit": "px"},
                    {"id": "pixelSpacing","label": "Pixel Spacing", "type": "number", "min": 0.05,"max": 1.0,  "step": 0.01, "default": 0.148,"unit": "mm"},
                ]
            },
            {
                "name": "Exposure",
                "fields": [
                    {"id": "kvp",         "label": "kVp",           "type": "number", "min": 40,  "max": 150,  "step": 1,    "default": 120,  "unit": "kV"},
                    {"id": "mas",         "label": "mAs",           "type": "number", "min": 0.5, "max": 200,  "step": 0.5,  "default": 4,    "unit": "mAs"},
                    {"id": "exposureTime","label": "Exposure Time", "type": "number", "min": 1,   "max": 500,  "step": 1,    "default": 20,   "unit": "ms"},
                ]
            }
        ]
    },

    "DX": {
        "label": "Digital X-Ray",
        "description": "2D X-ray — direct digital flat panel",
        "color": "#79c0ff",
        "icon": "🦴",
        "sopClass": "1.2.840.10008.5.1.4.1.1.1.1",
        "groups": [
            {
                "name": "Anatomy",
                "fields": [
                    {"id": "bodyPart",    "label": "Body Part",     "type": "select",
                     "options": ["CHEST","HAND","KNEE","FOOT","PELVIS","SPINE","SKULL"],
                     "default": "CHEST"},
                    {"id": "viewPosition","label": "View Position", "type": "select",
                     "options": ["PA","AP","LAT","OBLIQUE"],         "default": "PA"},
                ]
            },
            {
                "name": "Image",
                "fields": [
                    {"id": "rows",        "label": "Rows",          "type": "number", "min": 512, "max": 4096, "step": 1,   "default": 2480, "unit": "px"},
                    {"id": "cols",        "label": "Columns",       "type": "number", "min": 512, "max": 4096, "step": 1,   "default": 2560, "unit": "px"},
                    {"id": "pixelSpacing","label": "Pixel Spacing", "type": "number", "min": 0.05,"max": 1.0,  "step":0.01, "default": 0.139,"unit": "mm"},
                ]
            },
            {
                "name": "Exposure",
                "fields": [
                    {"id": "kvp",         "label": "kVp",           "type": "number", "min": 40,  "max": 150,  "step": 1,   "default": 125,  "unit": "kV"},
                    {"id": "mas",         "label": "mAs",           "type": "number", "min": 0.5, "max": 200,  "step": 0.5, "default": 3.2,  "unit": "mAs"},
                    {"id": "exposureTime","label": "Exposure Time", "type": "number", "min": 1,   "max": 500,  "step": 1,   "default": 16,   "unit": "ms"},
                ]
            }
        ]
    },

    "CT": {
        "label": "Computed Tomography",
        "description": "Multi-slice volumetric X-ray",
        "color": "#ffa657",
        "icon": "🔵",
        "sopClass": "1.2.840.10008.5.1.4.1.1.2",
        "groups": [
            {
                "name": "Protocol",
                "fields": [
                    {"id": "bodyPart",       "label": "Body Part",       "type": "select",
                     "options": ["CHEST","ABDOMEN","HEAD","NECK","SPINE","PELVIS","EXTREMITY"],
                     "default": "CHEST"},
                    {"id": "sliceCount",     "label": "Slice Count",     "type": "number", "min": 1,   "max": 512,  "step": 1,    "default": 64},
                    {"id": "sliceThickness", "label": "Slice Thickness", "type": "number", "min": 0.5, "max": 10,   "step": 0.25, "default": 1.25, "unit": "mm"},
                    {"id": "pitch",          "label": "Pitch",           "type": "number", "min": 0.5, "max": 2.0,  "step": 0.1,  "default": 1.0},
                ]
            },
            {
                "name": "Image",
                "fields": [
                    {"id": "rows", "label": "Rows",    "type": "number", "min": 256, "max": 1024, "step": 1, "default": 512, "unit": "px"},
                    {"id": "cols", "label": "Columns", "type": "number", "min": 256, "max": 1024, "step": 1, "default": 512, "unit": "px"},
                    {"id": "fov",  "label": "FOV",     "type": "number", "min": 100, "max": 500,  "step": 5, "default": 360, "unit": "mm"},
                ]
            },
            {
                "name": "Technique",
                "fields": [
                    {"id": "kvp",          "label": "kVp",           "type": "number", "min": 80,  "max": 140, "step": 5,   "default": 120, "unit": "kV"},
                    {"id": "mas",          "label": "mAs",           "type": "number", "min": 10,  "max": 500, "step": 10,  "default": 200, "unit": "mAs"},
                    {"id": "rotationTime", "label": "Rotation Time", "type": "number", "min": 0.3, "max": 1.0, "step": 0.1, "default": 0.5, "unit": "s"},
                ]
            }
        ]
    },

    "MR": {
        "label": "Magnetic Resonance",
        "description": "Multi-sequence MRI",
        "color": "#7ee787",
        "icon": "🧲",
        "sopClass": "1.2.840.10008.5.1.4.1.1.4",
        "groups": [
            {
                "name": "Protocol",
                "fields": [
                    {"id": "bodyPart",       "label": "Body Part",     "type": "select",
                     "options": ["BRAIN","SPINE","KNEE","SHOULDER","ABDOMEN","PELVIS","CARDIAC"],
                     "default": "BRAIN"},
                    {"id": "sequenceType",   "label": "Sequence",      "type": "select",
                     "options": ["SE","GRE","EPI","FLAIR","DWI","STIR","MPRAGE"],
                     "default": "SE"},
                    {"id": "sliceCount",     "label": "Slice Count",   "type": "number", "min": 1,  "max": 200, "step": 1,   "default": 20},
                    {"id": "sliceThickness", "label": "Slice Thick.",  "type": "number", "min": 0.5,"max": 10,  "step": 0.5, "default": 5, "unit": "mm"},
                ]
            },
            {
                "name": "Image",
                "fields": [
                    {"id": "rows",          "label": "Rows",          "type": "number", "min": 64, "max": 512, "step": 1, "default": 256, "unit": "px"},
                    {"id": "cols",          "label": "Columns",       "type": "number", "min": 64, "max": 512, "step": 1, "default": 256, "unit": "px"},
                    {"id": "fieldStrength", "label": "Field Strength","type": "select",
                     "options": ["1.5","3.0","7.0"],                                              "default": "1.5", "unit": "T"},
                ]
            },
            {
                "name": "Pulse Sequence",
                "fields": [
                    {"id": "tr",         "label": "TR",         "type": "number", "min": 10,  "max": 10000,"step": 10,  "default": 500, "unit": "ms"},
                    {"id": "te",         "label": "TE",         "type": "number", "min": 1,   "max": 500,  "step": 1,   "default": 15,  "unit": "ms"},
                    {"id": "flipAngle",  "label": "Flip Angle", "type": "number", "min": 1,   "max": 180,  "step": 1,   "default": 90,  "unit": "°"},
                ]
            }
        ]
    },

    "US": {
        "label": "Ultrasound",
        "description": "Real-time B-mode imaging",
        "color": "#d2a8ff",
        "icon": "🔊",
        "sopClass": "1.2.840.10008.5.1.4.1.1.6.1",
        "groups": [
            {
                "name": "Protocol",
                "fields": [
                    {"id": "bodyPart",  "label": "Body Part",   "type": "select",
                     "options": ["ABDOMEN","PELVIS","CARDIAC","THYROID","CAROTID","BREAST","VASCULAR"],
                     "default": "ABDOMEN"},
                    {"id": "probeType", "label": "Probe Type",  "type": "select",
                     "options": ["Convex","Linear","Phased","Endocavitary"],
                     "default": "Convex"},
                ]
            },
            {
                "name": "Image",
                "fields": [
                    {"id": "rows",      "label": "Rows",        "type": "number", "min": 240, "max": 1080, "step": 1,   "default": 480, "unit": "px"},
                    {"id": "cols",      "label": "Columns",     "type": "number", "min": 320, "max": 1920, "step": 1,   "default": 640, "unit": "px"},
                    {"id": "depth",     "label": "Depth",       "type": "number", "min": 2,   "max": 30,   "step": 0.5, "default": 15,  "unit": "cm"},
                    {"id": "frameRate", "label": "Frame Rate",  "type": "number", "min": 5,   "max": 60,   "step": 1,   "default": 30,  "unit": "fps"},
                ]
            },
            {
                "name": "Transducer",
                "fields": [
                    {"id": "frequency",    "label": "Frequency",    "type": "number", "min": 1,  "max": 15,  "step": 0.5, "default": 3.5, "unit": "MHz"},
                    {"id": "gain",         "label": "Gain",         "type": "number", "min": 0,  "max": 100, "step": 1,   "default": 50,  "unit": "dB"},
                    {"id": "dynamicRange", "label": "Dynamic Range","type": "number", "min": 40, "max": 100, "step": 1,   "default": 60,  "unit": "dB"},
                ]
            }
        ]
    }
}
