import asyncio
import json
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from engine.litefinance_gateway import LiteFinanceGateway

async def inspect():
    gw = LiteFinanceGateway(chrome_path='/usr/bin/google-chrome', proxy=None)
    await gw.initialize()
    
    # Dump right trade panel elements
    data = await gw._page.evaluate("""() => {
        const els = Array.from(document.querySelectorAll('button, a, input, label')).map(el => ({
            tag: el.tagName,
            id: el.id,
            className: el.className,
            text: (el.innerText || el.value || '').trim().slice(0, 100),
            type: el.getAttribute('type'),
            for: el.getAttribute('for'),
            w: el.getBoundingClientRect().width,
            h: el.getBoundingClientRect().height,
            x: el.getBoundingClientRect().x,
            y: el.getBoundingClientRect().y,
        })).filter(x => x.w > 0 && x.x > 800);
        return els;
    }""")
    
    print("RIGHT PANEL ELEMENTS:")
    for item in data:
        if any(kw in item['text'].upper() or kw in item['id'].upper() or kw in item['className'].upper() for kw in ['BUY', 'SELL', 'TRADE', 'ORDER', 'SUBMIT']):
            print(json.dumps(item))
            
    await gw.close()

if __name__ == "__main__":
    asyncio.run(inspect())
