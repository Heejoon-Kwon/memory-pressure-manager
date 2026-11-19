"""Fetch third-party public model pricing on demand; no keys or prompts sent."""
import json
import math
import threading
import time
import urllib.request

CATALOG_URL = 'https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json'
MAX_BYTES = 8 * 1024 * 1024
CACHE_SECONDS = 3600
_lock = threading.Lock()
_cache = None
_cached_at = 0.0

class PricingUnavailable(RuntimeError): pass

def rates_for(model):
    global _cache, _cached_at
    with _lock:
        now = time.monotonic()
        if _cache is None or now - _cached_at >= CACHE_SECONDS:
            try:
                req = urllib.request.Request(CATALOG_URL, headers={'Accept': 'application/json', 'User-Agent': 'JARVIS-pricing/1'})
                with urllib.request.urlopen(req, timeout=10) as response:
                    payload = response.read(MAX_BYTES + 1)
                if len(payload) > MAX_BYTES: raise ValueError('catalog too large')
                catalog = json.loads(payload)
                if not isinstance(catalog,dict): raise ValueError('invalid catalog')
            except Exception as e:
                raise PricingUnavailable('모델 가격표를 가져오지 못했습니다. 인터넷 연결을 확인하세요.') from e
            _cache = catalog; _cached_at = now
        entry = _cache.get(model)
        if not isinstance(entry,dict) or entry.get('litellm_provider') != 'openai':
            raise PricingUnavailable('선택한 OpenAI 모델의 가격이 가격표에 없습니다.')
        try:
            inp=float(entry['input_cost_per_token'])*1_000_000
            out=float(entry['output_cost_per_token'])*1_000_000
            if not (math.isfinite(inp) and math.isfinite(out) and inp>0 and out>0): raise ValueError('invalid price')
        except (KeyError,TypeError,ValueError,OverflowError) as e:
            raise PricingUnavailable('선택한 모델의 유효한 입력·출력 가격을 찾지 못했습니다.') from e
        return inp,out
