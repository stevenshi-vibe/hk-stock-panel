"""
RAG Knowledge Base — stores reference reports and methodology for AI-enhanced report generation.
Uses simple TF-IDF + keyword matching for retrieval (no vector DB needed for this scale).
"""
import os
import re
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

KNOWLEDGE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'knowledge_base')
INDEX_PATH = os.path.join(KNOWLEDGE_DIR, '_index.json')


def _ensure_kb():
    os.makedirs(KNOWLEDGE_DIR, exist_ok=True)


def init_knowledge_base():
    """Initialize knowledge base with default methodology documents."""
    _ensure_kb()
    # Write core methodology doc (desensitized, generic - no specific stock names/shares)
    methodology = """# CCASS数据分析方法论

## 数据口径与时效
- CCASS数据按T+2交收对齐到成交日。周末、全日休市和半日不交收日都不计入交收日
- 周末无CCASS更新
- CCASS参与者是托管行（花旗/汇丰等），不是买卖经纪商。一个托管行席位下可能混有多只基金
- 买入经纪商≠看好（客户可能是卖方通过该行挂单卖）

## 核心陷阱
1. **托管行 ≠ 买卖经纪商**：CCASS席位是托管行，富途十大行是当日报单成交通道，两者不同维度
2. **CCASS净流入 ≠ 新资金买入**：Pre-IPO股东实物股票电子化（从过户处转入CCASS）也会导致持仓增加，这是从非流通变可流通，不是新资金
3. **CCASS日期是交收日对齐后的成交日**：不是港交所页面上的持股日期本身，公众假期不会单列一条
4. **南向净买入 ≠ 主力加仓**：只是内地渠道方向，不覆盖外资/本地资金
5. **5%以下股东增减持无披露义务**

## 分析框架
1. 先确认CCASS总持仓占比变化（排除电子化转入干扰）
2. 对席位变动做归因：结合电子化PDF/权益披露/席位独占性判断是哪类股东
3. 成本分层：按入股价将股东分为深套/浅套/持平/获利/暴利，判断卖出意愿
4. 解禁节奏：解禁后1-2周是电子化高峰期，1个月内是减持观察窗口
5. 抛压评估 = 浮盈幅度 × 已解禁比例 × 席位流出速度

## 归因confidence分级
- high: 用户确认/PDF电子化文件直接对应
- med: 强推断（席位独占+成本匹配+背景匹配）
- low: 粗略估算（多股东共池，按比例分摊）

## 股东类型特征
- Pre-IPO/VC: 解禁后减持动力强，尤其浮盈>50%时
- 基石投资者: 6个月锁定期后通常分批减持
- 管理层: 减持幅度通常较小（控股权考虑）
- 战略投资者(如腾讯): 长期持有，偶尔微调
- ESOP/员工平台: 分批解禁，卖出节奏分散
- 港股通(南向): 短线交易为主，追涨杀跌

## 席位特征参考
- 花旗C00010: 常见外资托管行，多个基金共用
- 汇丰C00019: 大盘股常见托管，含战略投资者
- 中金B01654: 中资背景，含Pre-IPO/PE
- 华泰B01829: 常见VC/PE和散户混合
- 富途B01955/华盛/老虎: 散户为主
- 高盛/摩根士丹利/瑞银: 机构客户
"""

    writing_style = """# 专业分析报告写作规范

## 结构要求
1. 摘要/核心结论先行，忙碌的人只看前三行
2. 数据支撑结论，每个判断附数字
3. 风险提示放正文（是结论的一部分）
4. 方法论/口径放附录

## 写作原则
- 正文准入标准：删掉这段后故事线还能否自洽？能→移附录；不能→留正文
- 表格>段落，用数字不用模糊词（不说"市场很大"，说具体数字）
- 至少用两个数据源交叉对比
- 没有数据支撑不猜因果
- 不给出明确买卖建议，保持客观分析立场
- 区分已知事实（带数据来源）vs 推断（标注⚠️）

## 语言风格
- 专业但不晦涩，用短句
- 关键数字用**加粗**标注
- 利空信号用🔴，利好信号用🟢
- 减持/增持说明幅度（多少万股、占比多少）
- 成本/价格用HK$开头
- 避免主观情绪化用词（"暴涨""暴跌""疯狂"），使用客观描述
"""

    captable_framework = """# 成本结构与抛压分析框架

## 核心指标
- 加权平均成本（WAC）：各股东成本×持股加权
- 浮盈/浮亏比例：(现价-成本)/成本
- 成本区间分布：深套(<-30%)/被套(-30%~-5%)/持平(-5%~5%)/小赚(5%~30%)/获利(30%~80%)/暴利(>80%)
- 解禁日程：距今天数、类型、持股量
- 抛压评分(0-100)：成本分布40%+解禁25%+近期减持速度20%+CCASS流出15%

## 成本分析逻辑
1. 暴利区股东(>80%浮盈)减持动力最强
2. 已解禁+暴利 = 最高抛压
3. 深套区股东(< -30%)可能割肉但更可能扛着
4. 持平区股东是边际定价者

## 情景分析三档
- 乐观：仅解禁股东小幅减持(30%)，获利盘卖20%，市场承接力强
- 基准：解禁减持60%+获利盘卖30%，南向/机构部分承接
- 悲观：集中减持90%+获利盘卖50%，情绪恶化触发连锁抛售
"""

    capital_flow_framework = """# 资金流向分析框架

## 核心关注点
1. 净买入Top席位：谁在建仓？归因后判断是新机构/老股东换仓/南向
2. 净卖出Top席位：谁在减持？归因后判断是哪个老股东在卖
3. 集中度变化：Top10持仓占比变化反映筹码集中/分散
4. 散户席位(富途/华盛/老虎)净流入/流出反映散户情绪
5. 机构席位(高盛/大摩/里昂)流向反映机构态度

## 判断逻辑
- 单一席位单日变动>1%总股本 = 高优先级异动
- 新出席位首次持仓>0.2% = 新建仓信号
- 席位清仓(前一日>0.3%当日=0) = 减持完成信号
- 连续3日同方向变动 = 趋势性买卖
- 放量(>2x30日均量)配合价格方向 = 方向性信号
"""

    docs = {
        'methodology.md': methodology,
        'writing_style.md': writing_style,
        'cost_framework.md': captable_framework,
        'capital_flow_framework.md': capital_flow_framework,
    }

    for filename, content in docs.items():
        path = os.path.join(KNOWLEDGE_DIR, filename)
        if not os.path.exists(path):
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)

    _build_index()
    logger.info(f"Knowledge base initialized with {len(docs)} documents")


def add_document(name: str, content: str, category: str = 'custom'):
    """Add a custom document to knowledge base."""
    _ensure_kb()
    # Desensitize: replace potential stock-specific numbers/names with generic markers
    sanitized = _desensitize(content)
    path = os.path.join(KNOWLEDGE_DIR, f"{category}_{name}.md")
    with open(path, 'w', encoding='utf-8') as f:
        f.write(sanitized)
    _build_index()
    return path


def _desensitize(text: str) -> str:
    """Remove stock-specific sensitive data."""
    # Replace specific stock codes with [STOCK_CODE]
    text = re.sub(r'\b0\d{4}\.HK\b', '[STOCK].HK', text)
    text = re.sub(r'\b0\d{4}\b(?!\d)', '[STOCK]', text)
    # Replace specific shareholder names patterns (Chinese names followed by holdings data)
    # Don't try to be perfect - just catch obvious patterns
    return text


def _build_index():
    """Build simple keyword index for retrieval."""
    _ensure_kb()
    index = {}
    for filename in os.listdir(KNOWLEDGE_DIR):
        if filename.startswith('_') or not filename.endswith('.md'):
            continue
        path = os.path.join(KNOWLEDGE_DIR, filename)
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        # Extract keywords (simple: unique Chinese/English meaningful terms)
        words = set(re.findall(r'[\u4e00-\u9fff]{2,}|[a-zA-Z]{3,}', content))
        index[filename] = {
            'words': list(words),
            'content': content[:500],  # preview
        }
    with open(INDEX_PATH, 'w', encoding='utf-8') as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


def retrieve_relevant(query: str, top_k: int = 3) -> list[str]:
    """Retrieve relevant knowledge base sections for a given query."""
    _ensure_kb()
    if not os.path.exists(INDEX_PATH):
        _build_index()

    with open(INDEX_PATH, 'r', encoding='utf-8') as f:
        index = json.load(f)

    query_words = set(re.findall(r'[\u4e00-\u9fff]{2,}|[a-zA-Z]{3,}', query.lower()))

    # Score documents by word overlap
    scores = []
    for filename, doc in index.items():
        doc_words = set(w.lower() for w in doc['words'])
        overlap = len(query_words & doc_words)
        # Also check for direct substring match in content
        path = os.path.join(KNOWLEDGE_DIR, filename)
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            # Bonus for direct phrase matches
            for qw in query_words:
                if qw in content.lower():
                    overlap += 2
        if overlap > 0:
            scores.append((overlap, filename))

    scores.sort(reverse=True)
    results = []
    for _, filename in scores[:top_k]:
        path = os.path.join(KNOWLEDGE_DIR, filename)
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                results.append(f.read())
    return results


def get_rag_context(query: str, report_type: str = 'weekly', mode: str = 'trader') -> str:
    """Get formatted RAG context for LLM prompt."""
    queries = [query, f'{report_type} report {mode}']
    all_docs = []
    seen = set()
    for q in queries:
        docs = retrieve_relevant(q, top_k=2)
        for d in docs:
            if d not in seen:
                all_docs.append(d)
                seen.add(d)

    if not all_docs:
        return ''

    context = "\n\n---\n\n".join(all_docs[:4])
    return f"""## Reference Knowledge Base (methodology and writing style guides)
Use the following framework and style guidelines when writing your analysis. These are established methodology rules:

{context}

## Important Rules
- Follow the data interpretation rules above (especially T+2, 托管行≠经纪商, CCASS净流入≠买入)
- Use the writing style: conclusion first, data-backed, objective, no buy/sell recommendations
- Follow the cost structure and capital flow analysis frameworks
- All analysis must cite specific numbers (shares, percentages, HKD amounts)
"""


if __name__ == '__main__':
    init_knowledge_base()
    print("Knowledge base initialized")
    results = retrieve_relevant("成本 浮盈 抛压 减持")
    print(f"Retrieved {len(results)} docs for cost/selloff query")
    for i, r in enumerate(results):
        print(f"\n--- Doc {i+1} ({len(r)} chars) ---")
        print(r[:200])
