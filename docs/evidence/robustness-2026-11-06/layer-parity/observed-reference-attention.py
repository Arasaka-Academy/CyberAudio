def diagnostic_attention(
    self,
    x: mx.array,
    position_embeddings: Tuple[mx.array, mx.array],
    mask: Optional[mx.array] = None,
    cache: Optional[KVCache] = None,
) -> mx.array:
    batch, seq_len, _ = x.shape

    # Project to Q, K, V
    q = self.q_proj(x)
    k = self.k_proj(x)
    v = self.v_proj(x)

    # Reshape to [batch, seq_len, num_heads, head_dim]
    q = q.reshape(batch, seq_len, self.num_heads, self.head_dim)
    k = k.reshape(batch, seq_len, self.num_kv_heads, self.head_dim)
    v = v.reshape(batch, seq_len, self.num_kv_heads, self.head_dim)

    attn("input", x)
    attn("q_raw", q)
    attn("k_raw", k)
    attn("v_raw", v)
    # Apply QK normalization
    q = self.q_norm(q)
    k = self.k_norm(k)
    attn("q_norm", q)
    attn("k_norm", k)

    # Transpose to [batch, num_heads, seq_len, head_dim]
    q = mx.transpose(q, (0, 2, 1, 3))
    k = mx.transpose(k, (0, 2, 1, 3))
    v = mx.transpose(v, (0, 2, 1, 3))

    # Apply rotary embeddings (interleaving already done in TalkerRotaryEmbedding)
    cos, sin = position_embeddings
    q, k = apply_multimodal_rotary_pos_emb(q, k, cos, sin)
    attn("q_rotary", q)
    attn("k_rotary", k)

    # Handle KV cache using mlx_lm's KVCache
    if cache is not None:
        k, v = cache.update_and_fetch(k, v)

    attn("k_cache", k)
    attn("v_cache", v)
    # Use fast scaled dot product attention with GQA support
    output = mx.fast.scaled_dot_product_attention(
        q, k, v, scale=self.scale, mask=mask
    )

    attn("sdpa", output)
    # Reshape back
    output = mx.transpose(output, (0, 2, 1, 3))
    output = output.reshape(batch, seq_len, -1)

    projected = self.o_proj(output)
    attn("projected", projected)
    return projected
