changed = source.read_text() + r'''
extension Qwen3TTSModel {
    public func diagnosticTalkerForward(
        inputs: [MLXArray], traceSteps: Set<Int>,
        onStep: (Int, MLXArray, MLXArray, [String: MLXArray]) throws -> Void
    ) throws {
        try requireGenerationComponents()
        let cache = talker.makeCache()
        for (index, input) in inputs.enumerated() {
            var traced = [String: MLXArray]()
            DiagnosticTalkerTrace.sink = traceSteps.contains(index) ? { name, value in traced[name] = value } : nil
            let (logits, hidden) = talker(input, cache: cache)
            MLX.eval(logits, hidden)
            DiagnosticTalkerTrace.sink = nil
            try onStep(index, logits[0..., (-1)..., 0...], hidden[0..., (-1)..., 0...], traced)
        }
    }
}
'''
private = root / 'Qwen3TTS.swift'
private.write_text(changed)
talker_changed = talker.read_text()


def edit(before, after):
    global talker_changed
    assert talker_changed.count(before) == 1, before
    talker_changed = talker_changed.replace(before, after)


edit('// MARK: - RoPE helpers', r'''
enum DiagnosticTalkerTrace {
    nonisolated(unsafe) static var sink: ((String, MLXArray) -> Void)?
    nonisolated(unsafe) static var layer = 0
    static func record(_ name: String, _ value: MLXArray) { sink?(name, value) }
    static func attention(_ name: String, _ value: MLXArray) {
        sink?(String(format: "layer-%02d.attn.%@", layer, name), value)
    }
}

// MARK: - RoPE helpers
''')
edit('        q = qNorm(q)\n        k = kNorm(k)', '''        DiagnosticTalkerTrace.attention("input", x)
        DiagnosticTalkerTrace.attention("q_raw", q)
        DiagnosticTalkerTrace.attention("k_raw", k)
        DiagnosticTalkerTrace.attention("v_raw", v)
        q = qNorm(q)
        k = kNorm(k)
        DiagnosticTalkerTrace.attention("q_norm", q)
        DiagnosticTalkerTrace.attention("k_norm", k)''')
edit('        if let cache {\n            (k, v) = cache.update(keys: k, values: v)\n        }', '''        DiagnosticTalkerTrace.attention("q_rotary", q)
        DiagnosticTalkerTrace.attention("k_rotary", k)
        if let cache {
            (k, v) = cache.update(keys: k, values: v)
        }
        DiagnosticTalkerTrace.attention("k_cache", k)
        DiagnosticTalkerTrace.attention("v_cache", v)''')
edit('        return oProj(output.transposed(0, 2, 1, 3).reshaped(batch, seqLen, -1))', '''        DiagnosticTalkerTrace.attention("sdpa", output)
        let projected = oProj(output.transposed(0, 2, 1, 3).reshaped(batch, seqLen, -1))
        DiagnosticTalkerTrace.attention("projected", projected)
        return projected''')
edit('        let posEmbeddings = rotaryEmb(inputsEmbeds, positionIds: posIds)', '''        let posEmbeddings = rotaryEmb(inputsEmbeds, positionIds: posIds)
        DiagnosticTalkerTrace.record("position_ids", posIds)
        DiagnosticTalkerTrace.record("inv_freq", rotaryEmb._invFreq)
        DiagnosticTalkerTrace.record("cos", posEmbeddings.0)
        DiagnosticTalkerTrace.record("sin", posEmbeddings.1)''')
edit('            x = layer(x, positionEmbeddings: posEmbeddings, mask: causalMask, cache: cache?[i])', '''            DiagnosticTalkerTrace.layer = i
            DiagnosticTalkerTrace.record(String(format: "layer-%02d.input", i), x)
            x = layer(x, positionEmbeddings: posEmbeddings, mask: causalMask, cache: cache?[i])
            DiagnosticTalkerTrace.record(String(format: "layer-%02d.output", i), x)''')
private_talker = root / 'Qwen3TTSTalker.swift'
private_talker.write_text(talker_changed)
