"""Generate only reference DSP constants; no MLX/model import or weights."""
from pathlib import Path
import hashlib
import json
import struct

import numpy as np
from transformers.audio_utils import window_function, mel_filter_bank

base = Path(__file__).resolve().parent
window = window_function(400, 'hann').astype('<f4')
filters = mel_filter_bank(201, 128, 0.0, 8000.0, 16000, norm='slaney', mel_scale='slaney').astype('<f4')
arrays = {'window': window, 'filters': filters}
header, payload = {}, bytearray()
for name, array in arrays.items():
    start = len(payload)
    payload.extend(array.tobytes())
    header[name] = {'dtype': 'F32', 'shape': list(array.shape), 'data_offsets': [start, len(payload)]}
encoded = json.dumps(header).encode()
encoded += b' ' * (-len(encoded) % 8)
data = struct.pack('<Q', len(encoded)) + encoded + payload
path = base / 'reference-constants.safetensors'
assert not path.exists()
path.write_bytes(data)
print('Reference constants',len(data),'bytes',hashlib.sha256(data).hexdigest())
