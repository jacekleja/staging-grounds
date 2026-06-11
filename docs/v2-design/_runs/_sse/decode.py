import sys, json, re

def parse(path):
    chunks = []
    meta = None
    nopreview = None
    refuse_texts = []
    for raw in open(path, encoding='utf-8'):
        raw = raw.strip()
        if not raw.startswith('data: '):
            continue
        body = raw[6:]
        try:
            o = json.loads(body)
        except Exception:
            continue
        if isinstance(o, str) and not o.startswith('[DONE'):
            chunks.append(o)
        elif isinstance(o, dict):
            if '__meta__' in o:
                meta = o['__meta__']
            if '__no_preview__' in o:
                nopreview = o['__no_preview__']
    text = ''.join(chunks)
    return text, meta, nopreview

def main():
    path = sys.argv[1]
    text, meta, nopreview = parse(path)
    tc = (meta or {}).get('turn_classification', {})
    ts = tc.get('tier_signals') or {}
    cache = (meta or {}).get('cache', {}).get('status')
    print('ROUTING mode={} tier={} composition={} cache={}'.format(
        tc.get('mode'), tc.get('tier'), tc.get('composition'), cache))
    print('SIGNALS result_count={} product_count={} max_axis_top_share={}'.format(
        ts.get('result_count'), ts.get('product_count'), ts.get('max_axis_top_share')))
    print('NO_PREVIEW={} llm_call_count={} text_len={}'.format(
        bool(nopreview), tc.get('llm_call_count'), len(text)))
    m = re.search(r'```lbjson\s*(\{.*?\})\s*```', text, re.S)
    if m:
        try:
            b = json.loads(m.group(1))
            print('LBJSON_KEYS', json.dumps(sorted(b.keys())))
            print('HAS_chat_affordance', 'chat_affordance' in b)
            if 'chat_affordance' in b:
                print('chat_affordance', json.dumps(b['chat_affordance'], ensure_ascii=False))
            print('HAS_type_it_out', 'type_it_out' in b)
            if 'type_it_out' in b:
                print('type_it_out', json.dumps(b['type_it_out'], ensure_ascii=False))
            print('HAS_hatch', 'hatch' in b)
            print('shape', b.get('shape'))
        except Exception as e:
            print('LBJSON_PARSE_ERR', e)
    else:
        print('NO_LBJSON_BLOCK_IN_TEXT')
    # surface raw assembled text for refuse/unsafe inspection
    print('---TEXT_BEGIN---')
    print(text)
    print('---TEXT_END---')
    # explicit chat_affordance presence scan across the entire decoded text
    print('RAW_TEXT_HAS_chat_affordance_substr', 'chat_affordance' in text)

if __name__ == '__main__':
    main()
