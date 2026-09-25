import asyncio
import json
from engine.litefinance_gateway import LiteFinanceGateway

async def main():
    gw = LiteFinanceGateway(chrome_path='/usr/bin/google-chrome', proxy=None)
    await gw.initialize()
    acc = await gw.get_account_snapshot(force_fresh=True)
    print(f"SNAPSHOT: Balance={acc.balance} Equity={acc.equity} Used={acc.assets_used}")
    
    info = await gw._page.evaluate("""() => {
        const text = document.body ? document.body.innerText : '';
        const lines = text.split('\\n').map(l => l.trim()).filter(l => l.length > 0);
        return {
            has_demo: text.includes('DEMO ACCOUNT') || text.includes('Activate real trading'),
            has_real: text.includes('REAL ACCOUNT') || text.includes('Activate demo trading'),
            top_lines: lines.slice(0, 25),
        };
    }""")
    print("ACCOUNT INFO:", json.dumps(info, indent=2))
    await gw._page.screenshot(path="/root/ict_sniper/data/account_check.png")
    await gw.close()

if __name__ == '__main__':
    asyncio.run(main())
