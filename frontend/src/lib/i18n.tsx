import { createContext, useContext, useState, useEffect, ReactNode } from 'react'

// English aliases for common CCASS participant IDs (HKEX traditional Chinese -> English).
// When lang='en', use these names wherever broker names appear (overview, capital flow, CCASS, signals, cost analysis).
type Lang = 'zh' | 'en'

export const BROKER_NAME_EN: Record<string, string> = {
  C00010: 'Citibank',
  C00019: 'HSBC Hong Kong',
  C00033: 'Standard Chartered Bank (HK)',
  C00039: 'Bank of China (HK)',
  C00042: 'CMB Wing Lung Bank',
  C00088: 'China Merchants Bank',
  C00093: 'BNP Paribas',
  C00111: 'Societe Generale',
  C00113: 'ABN AMRO Clearing Bank',
  C00155: 'Hang Seng Bank',
  B01130: 'BOC International',
  B01224: 'MLFE (Merrill Lynch)',
  B01228: 'CITIC Securities (HK)',
  B01274: 'Morgan Stanley HK',
  B01487: 'China Renaissance Securities (HK)',
  B01590: 'Interactive Brokers HK',
  B01610: 'KGI Asia',
  B01654: 'CICC HK Securities',
  B01829: 'Huatai Financial Holdings (HK)',
  B01893: 'China Merchants Securities (HK)',
  B01904: 'Valuable Capital',
  B01955: 'Futu Securities (HK)',
  B01963: 'TF International Securities',
  B02009: 'Jin Yu Fu Securities',
  B02142: 'Tiger Brokers (HK)',
  A00003: 'CSDC (S/HK Connect)',
  A00004: 'CSDC (SZ/HK Connect)',
  A00005: 'CSDC (HK/Mainland Connect)',
}

// English stock names for well-known HKEX codes (Chinese names come from raw data; these are overrides in EN mode).
// Users can add more via the UI or by editing this map — no user-specific data is hardcoded.
export const STOCK_NAME_EN: Record<string, string> = {
  '00700': 'Tencent Holdings',
  '09988': 'Alibaba Group',
  '03690': 'Meituan',
  '01810': 'Xiaomi Corp',
  '09618': 'JD.com',
  '09888': 'Baidu Inc',
  '01024': 'Kuaishou Tech',
  '09999': 'NetEase',
  '00981': 'SMIC',
  '00941': 'China Mobile',
  '00005': 'HSBC Holdings',
  '00388': 'HKEX',
  '01299': 'AIA Group',
}

const translations = {
  zh: {
    // Common
    loading: '加载中...',
    back: '← 返回',
    settings: '⚙️ 设置',
    save: '💾 保存设置',
    saving: '保存中...',
    saved: '✅ 已保存',
    saveFailed: '保存失败',
    error: '错误',
    retry: '重试',
    noData: '暂无数据',
    confirm: '确定',
    cancel: '取消',

    // App
    appTitle: '港股 CCASS 监控面板',
    addStockPlaceholder: '例如 00700 腾讯 / 09988 阿里',
    searchPrompt: '🔍 输入港股代码开始追踪',
    query: '查询',
    added: '已添加 {count} 只股票 · 下方可快速进入',
    invalidCode: '请输入5位港股代码（如 00700）',
    addFailed: '添加失败',

    // Homepage
    heroSubtitle: '面向港股交易者与企业IR的一站式持仓监控工具。CCASS席位追踪、南向资金监控、老股东减持归因、异动信号检测、自动研报生成，全部本地部署，数据不出本机。',
    features: '核心功能',
    feature_ccass: { title: '实时CCASS监控', desc: '每日追踪中央结算系统持仓变化，席位级买卖动向一目了然' },
    feature_southbound: { title: '南向资金追踪', desc: '港股通持股比例变化、每日净买入/卖出，把握北水流向' },
    feature_anomaly: { title: '智能异动检测', desc: '自动识别大额席位变动、新入/退出机构、放量信号，0-100评分' },
    feature_selloff: { title: '老股东减持追踪', desc: '上传股东名册后自动归因席位，精确监控Pre-IPO/基石/管理层减持' },
    feature_unlock: { title: '解禁日历', desc: '管理锁到期日程，自动追踪解禁后席位流向变化' },
    feature_report: { title: '自动报告生成', desc: '一键生成日报/周报/月报，公开信息+减持数据合成分析报告' },
    traderMode: '交易者模式',
    traderModeDesc: [
      '✓ 输入代码即看行情+CCASS+南向',
      '✓ 异动信号评分辅助交易决策',
      '✓ 席位资金流向追踪',
      '✓ 公告事件股价标注',
      '✓ 一键生成交易日报/周报',
    ],
    irMode: 'IR模式',
    irModeDesc: [
      '✓ 上传股东名册自动席位归因',
      '✓ 老股东减持精确追踪',
      '✓ 解禁日历管理',
      '✓ Excel导出',
      '✓ IR专属周报/月报生成',
    ],
    myStocks: '📁 我的股票',
    irConfigured: 'IR已配置',
    publicMode: '公开模式',
    updatedAt: '更新于',
    footerPrivacy: '🔒 所有数据存储在本地SQLite · 无需登录 · 无云端传输',
    footerSource: '数据来源：HKEX 披露易 · 腾讯财经 · Yahoo Finance · CCASS Search',

    // Dashboard tabs
    tab_overview: '概览',
    tab_ccass: 'CCASS',
    tab_trading: '行情',
    tab_signals: '异动',
    tab_reports: '报告',
    tab_ir: 'IR追踪',
    tab_ir_setup: 'IR设置',
    tab_cost: '成本分析',
    tab_flow: '资金流向',
    updateData: '⟳ 更新数据',
    updatingData: '⟳ 更新中...',
    fetchingCcass: '正在抓取CCASS数据',
    noCcassData: '暂无CCASS数据',
    firstLoadHint: '首次加载需要2-5分钟（90个交易日），后续更新只需几分钟。',
    marketDataReady: '行情数据已就绪，可先查看"行情"标签。',
    startFetch: '开始抓取CCASS数据',
    fetchFailed: '数据抓取失败',

    // Overview / KPI
    ccassHoldings: 'CCASS持仓',
    ofTotalShares: '总股本',
    southboundHoldings: '南向持股',
    signalScore: '信号评分',
    noSignals: '无信号',
    issuedShares: '发行股份',
    latestAnomalies: '🚨 最新异动',
    high: '高',
    medium: '中',
    top10Holders: '🏦 Top 10 持仓席位',
    seat: '席位',
    holdings: '持仓',
    pct: '占比',
    pctNotDisclosed: '港交所未公布占比：已发行单位的最近更新数目落后于每日申赎，所以百分比一栏被省略。持仓数量仍然有效。',
    irEnabled: '🔒 IR追踪已启用',
    irEnabledDesc: '已归因股东席位覆盖 {shares} 股，切换到"IR追踪"标签查看减持数据。',
    irDisabled: '🔓 开启IR追踪功能',
    irDisabledDesc: '上传股东名册/券商账户表后，可以查看老股东减持追踪、解禁日历、归因分析等IR专属功能。支持 Excel、CSV、PDF 格式。',
    irDisabledNote: '无需账号，数据完全保存在本地。切换到"IR设置"标签上传文件。',

    // Price card
    open: '开',
    priceHigh: '高',
    priceLow: '低',
    volume: '成交',
    turnover: '额',
    avgVol30d: '30日均量',
    range: '区间',

    // CCASS tab
    southboundDailyNet: '南向资金每日净买卖 (万股)',
    ccassRange: '天数范围',
    ccassBackfillShort: '已有数据不足、正在补抓',
    ccassHoldingsTrend: 'CCASS 持股走势 (万股)',
    ccassDailyTable: 'CCASS 每日汇总',
    participantHoldings: '席位持仓明细',
    exportExcel: '📥 导出Excel',
    exporting: '导出中...',
    latestDate: '最新日期',
    showChanges: '有变动',
    showAll: '全部',
    dailyChange: '日变动',
    change3d: '3日变动',
    attribution: '归因',
    netBuy: '净买入',

    // Trading tab
    priceChart6m: '股价走势（近6个月）',
    close: '收盘',
    volume_short: '成交量',
    announcements: '📢 公司公告',
    fetchAnnouncements: '🔄 抓取公告',
    fetchingAnn: '抓取中...',
    noAnnouncements: '暂无公告数据，点击"抓取公告"获取港交所披露易数据',
    tradingDetails: '交易明细',
    date: '日期',
    change: '涨跌',
    // Announcement categories
    cat_earnings: '业绩公告',
    cat_buyback: '回购',
    cat_trade: '增减持',
    cat_placing: '配股/融资',
    cat_director: '董事变动',
    cat_material: '重大事项',
    cat_dividend: '股息',
    cat_other: '其他',

    // Signals tab
    signalScoreLabel: '异动信号评分',
    sellSignals: '📉 卖出信号',
    buySignals: '📈 买入信号',
    strongAccumulation: '强吸筹',
    neutral: '中性',
    strongDistribution: '强减持',
    detectedAnomalies: '🔍 检测到的异动',
    highPriority: '高优先级',
    midPriority: '中优先级',
    noAnomalies: '未检测到明显异动',
    signalExplanation: '📖 信号说明',
    scoreLogic: '<strong>评分逻辑：</strong>综合席位单日变动、3日趋势、新入/退出席位、南向资金流向、成交量异常计算0-100分。',
    highPriorityAnomaly: '<strong>高优先级异动：</strong>单席位单日变动超1%总股本，或3日累计变动超1%。',
    newParticipant: '<strong>新入席位：</strong>首次出现在CCASS持仓列表且持仓超0.2%，可能代表新机构建仓或老股东换仓。',
    participantExit: '<strong>席位清仓：</strong>前一日持有超0.3%但当日完全退出，可能是减持完成。',
    volumeAnomaly: '<strong>放量异常：</strong>成交量超过30日均量2倍，结合价格方向判断买卖力量。',
    signalDisclaimer: '⚠️ 信号仅供参考，不构成投资建议。CCASS数据为T+2披露。',

    // IR tab
    uploadTitle: '📤 上传股东名册/券商账户表',
    uploadDesc: '支持 Excel（.xlsx）、CSV、PDF。表头放在前 30 行内，需包含股东名称（姓名 / 股東名稱）和持股数量；席位号或参与者编号可选。识别成功后，记录出现在下方「股东归因」表，可以再改。再次上传不会覆盖你在面板里改过的记录，除非你确认用文件覆盖。',
    selectFile: '选择文件上传',
    uploadingParsing: '上传解析中...',
    addManualAttr: '+ 手动添加归因',
    parseSuccess: '✅ 解析成功',
    parseFailed: '❌ 解析失败',
    parseSummary: '识别到 {count} 条股东席位归因，{unlocks} 条解禁记录。',
    manualAddTitle: '手动添加股东席位归因',
    shareholderName: '股东名称',
    participantId: '席位编号(Participant ID)',
    participantName: '席位名称(如 高盛(亚洲)',
    sharesBaseline: '持股数量(基线)',
    costHkd: '成本价(HKD)',
    investorType: '投资者类型(如 Pre-IPO/基石/管理层)',
    unlockDate: '解禁日期(YYYY-MM-DD)',
    notes: '备注',
    edit: '编辑',
    delete: '删除',
    saveChanges: '保存',
    holdingPct: '持股比例(%)',
    editShareholder: '编辑股东',
    deleteShareholderTitle: '删除这条股东记录？',
    deleteShareholderBody: '将删除「{name}」（{seat}）。只属于该股东的减持和解禁记录会一起去掉。',
    confirmDelete: '确认删除',
    importKept: '有 {count} 条已在面板里改过，这次没有覆盖。备注会保留；持股、成本、解禁日需要确认后才用文件里的数覆盖。',
    overwriteManual: '用文件覆盖这些记录',
    overwriteDone: '已用文件覆盖 {count} 条手工修改，备注仍保留。',
    totalRow: '合计',
    actions: '操作',
    editedBadge: '已修改',
    pctHint: '已知总股本 {issued} 股。改持股数量或比例会互相换算。',
    pctHintNoIssued: '暂无总股本，比例按填写值保存，不会反推股数。',
    noAttribution: '尚未配置股东归因。上传文件或手动添加以开启减持追踪。',
    shareholderAttribution: '👥 股东席位归因',
    shareholder: '股东',
    currentHoldings: '当前持仓',
    baselineHoldings: '基线持股',
    changeFromBaseline: '较基线变化',
    change30d: '30日变动',
    cost: '成本',
    selloffTracking: '📉 减持追踪（每日）',
    closingPrice: '收盘',
    unlockCalendar: '📅 解禁日历',
    upcomingUnlocks: '即将解禁',
    pastUnlocks: '已解禁',

    // Reports tab
    autoGenerateReport: '📝 自动生成报告',
    reportType: '报告类型',
    daily: '日报',
    weekly: '周报',
    monthly: '月报',
    reportMode: '报告模式',
    traderView: '📈 交易者版',
    irView: '🏢 IR版',
    publicData: '公开数据',
    inclSelloff: '含股东减持',
    generateReport: '⚡ 生成报告',
    generating: '生成中...',
    generateFailed: '生成失败',
    irModeHint: 'IR版报告包含股东归因、减持追踪、解禁日历等IR专属数据，需先在IR设置中上传股东名册。',
    traderModeHint: '交易者版基于公开行情、CCASS、南向资金、公告和异动信号生成，不包含任何非公开数据。',
    webVersion: '🌐 网页版',
    downloadMd: '📥 下载Markdown',
    aiEnhanced: 'AI增强',
    template: '模板生成',
    aiEnhancedHint: 'AI增强模式使用大语言模型生成深度分析（需在设置中配置API）',
    reportPeriod: '报告期间',
    generatedAt: '生成时间',

    // Settings
    settingsTitle: '⚙️ 设置',
    outOfBox: '🔓 开箱即用',
    outOfBoxDesc: '本工具默认使用公开数据源（HKEX披露易、腾讯财经、Yahoo Finance），<strong>无需任何API Key即可使用全部核心功能</strong>。以下为可选高级配置，用于启用增强功能。',
    servicePort: '🌐 服务端口',
    listenPort: '监听端口',
    portRestart: '修改后需重启服务生效。当前访问：http://localhost:{port}',
    aiReport: '🤖 AI增强报告（可选）',
    aiReportDesc: '启用后，日报/周报/月报将使用大语言模型生成更深度的分析段落',
    enable: '启用',
    apiProvider: 'API 提供商',
    apiBaseUrl: 'API Base URL',
    apiKey: 'API Key',
    apiKeyLocal: '密钥仅保存在本地 config.json，不上传云端',
    modelName: '模型名称',
    provider_openai: 'OpenAI (GPT-4o)',
    provider_anthropic: 'Anthropic (Claude)',
    provider_deepseek: 'DeepSeek',
    provider_custom: '自定义 (OpenAI兼容)',
    notifications: '🔔 异动推送（可选）',
    notificationsDesc: '重大减持、新进机构等异动事件通过 Webhook 推送',
    webhookUrl: 'Webhook URL（飞书/企业微信/Slack）',
    companyInfo: '🏢 IR报告公司信息（可选）',
    companyInfoDesc: '填写后IR报告将显示公司信息',
    companyName: '公司名称',
    irEmail: 'IR联系邮箱',
    logoUrl: 'Logo URL（可选）',
    configFileLocation: '配置文件存储位置：<code className="bg-slate-100 px-1 rounded">backend/config.json</code>',
    allDataLocal: '所有数据本地存储，不会上传至任何云端服务',

    // Auto fetch
    autoFetch: '⏰ 每日自动更新',
    autoFetchDesc: '电脑保持开机并保持本程序运行时，会在每日指定时间自动抓取所有已添加股票的最新行情与CCASS数据',
    autoFetchTime: '每日更新时间',
    autoFetchDays: '回填天数（天）',
    autoFetchLastRun: '上次运行',
    autoFetchNextRun: '下次运行',
    autoFetchNever: '尚未运行',
    autoFetchRunNow: '▶ 立即手动更新全部',
    autoFetchRunning: '运行中…',
    autoFetchRunNowHint: '立即对所有已添加股票抓取最新数据',
    autoFetchDisabledHint: '开启后，程序会在您电脑开机且本页面/服务运行时每天自动更新数据；您也可以随时手动点击“更新数据”按钮',
    autoFetchTimeHint: '建议设在港股收盘(16:00)15分钟以后，等披露易数据更新后再抓取',
    autoFetchDaysHint: '自动抓取时向后回填的天数（首次建议 90 天）',

    // Cost Analysis
    costAnalysis: '成本结构与抛压分析',
    currentPrice: '当前价格',
    totalAnalyzedShares: '已分析持股',
    pressureScore: '抛压评分',
    pressureLow: '低抛压',
    pressureModerate: '中等抛压',
    pressureHigh: '高抛压',
    pressureExtreme: '极高抛压',
    costDistribution: '成本分布',
    deepUnderwater: '深度套牢',
    underwater: '浮亏',
    atCost: '成本附近',
    moderateProfit: '小幅盈利',
    goodProfit: '盈利丰厚',
    heavyProfit: '暴利区间',
    shareholderCostChart: '股东成本分布',
    costPrice: '成本价(HKD)',
    shareholderList: '股东明细',
    profitPct: '盈亏比例',
    floatingPnL: '浮动盈亏',
    pressureLevel: '抛压等级',
    broker: '席位/券商',
    investorTypePie: '投资者类型分布',
    brokerConcentration: '席位集中度',
    shareholderCount: '股东数',
    totalShares: '总持股',
    pressureHeavy: '重度',
    pressureModerateLevel: '中度',
    pressureLowLevel: '轻度',
    pressureUnderwater: '套牢',
    pressureUnknown: '未知',
    noCostData: '暂无成本数据',
    noAttributionData: '尚未配置股东归因数据，无法进行成本分析。请在IR设置中上传股东名册。',

    // Capital Flow
    capitalFlow: '资金流向分析',
    topInflows: '净买入Top10席位',
    topOutflows: '净卖出Top10席位',
    netChange: '净变动',
    currentShares: '当前持仓',
    attributedShareholder: '归因股东',
    dailyBrokerFlows: '席位每日净买卖（Top10）',
    shareholderFlow: '已知股东资金流向',
    periodRange: '统计区间',
    periodEnd: '截止日期',
    unknownRetail: '散户/未知',
    buying: '买入',
    selling: '卖出',
    noFlowData: '暂无资金流向数据',

    // Capital flow additions
    dailyNetFlow: '每日净流向汇总',
    dailyNetFlowHint: '所有上榜席位净变动加总（万股）。正值=资金净流入，负值=净流出',
    tenKUnit: '万',
    tenKShares: '万股',
    positiveHint: '正值为净买入，负值为净卖出',

    // Cost analysis
    tenKSharesShort: '万股',
    daysUnit: '天',
    scenarioFootnote: '* 情景分析基于成本结构、解禁计划和历史成交量估算，实际抛压受市场情绪和宏观环境影响。',
  },

  en: {
    // Common
    loading: 'Loading...',
    back: '← Back',
    settings: '⚙️ Settings / 设置',
    save: '💾 Save Settings',
    saving: 'Saving...',
    saved: '✅ Saved',
    saveFailed: 'Save failed',
    error: 'Error',
    retry: 'Retry',
    noData: 'No data',
    confirm: 'Confirm',
    cancel: 'Cancel',

    // App
    appTitle: 'HK Stock CCASS Monitor',
    addStockPlaceholder: 'e.g. 00700 Tencent / 09988 Alibaba',
    searchPrompt: '🔍 Enter HK stock code to start tracking',
    query: 'Search',
    added: '{count} stocks added · Quick access below',
    invalidCode: 'Please enter a 5-digit HK stock code (e.g. 00700)',
    addFailed: 'Failed to add stock',

    // Homepage
    heroSubtitle: 'An all-in-one position monitoring tool for HK stock traders and corporate IR teams. CCASS participant tracking, Southbound Connect monitoring, shareholder selloff attribution, anomaly signal detection, and automated report generation — all deployed locally, data stays on your machine.',
    features: 'Key Features',
    feature_ccass: { title: 'Real-time CCASS Monitoring', desc: 'Daily tracking of CCASS position changes with participant-level buy/sell visibility' },
    feature_southbound: { title: 'Southbound Connect Tracking', desc: 'Monitor HK Connect shareholding changes and daily net buy/sell flows' },
    feature_anomaly: { title: 'Smart Anomaly Detection', desc: 'Auto-identify large participant moves, new/exiting institutions, volume spikes with 0-100 scoring' },
    feature_selloff: { title: 'Shareholder Selloff Tracking', desc: 'Upload shareholder register for automatic seat attribution, precisely monitor Pre-IPO/cornerstone/management selling' },
    feature_unlock: { title: 'Unlock Calendar', desc: 'Manage lock-up expiry schedules and track post-unlock participant flows' },
    feature_report: { title: 'Automated Reports', desc: 'One-click daily/weekly/monthly reports combining public data and selloff analytics' },
    traderMode: 'Trader View',
    traderModeDesc: [
      '✓ Enter code to see market + CCASS + Southbound',
      '✓ Anomaly signal scoring aids trading decisions',
      '✓ Participant capital flow tracking',
      '✓ Announcement event annotations on charts',
      '✓ One-click daily/weekly trading reports',
    ],
    irMode: 'IR View',
    irModeDesc: [
      '✓ Upload shareholder register for auto attribution',
      '✓ Precise shareholder selloff tracking',
      '✓ Unlock calendar management',
      '✓ Excel export',
      '✓ IR-exclusive weekly/monthly reports',
    ],
    myStocks: '📁 My Stocks',
    irConfigured: 'IR Configured',
    publicMode: 'Public Mode',
    updatedAt: 'Updated',
    footerPrivacy: '🔒 All data stored in local SQLite · No login required · No cloud transmission',
    footerSource: 'Data sources: HKEX · Tencent Finance · Yahoo Finance · CCASS Search',

    // Dashboard tabs
    tab_overview: 'Overview',
    tab_ccass: 'CCASS',
    tab_trading: 'Market',
    tab_signals: 'Signals',
    tab_reports: 'Reports',
    tab_ir: 'IR Tracking',
    tab_ir_setup: 'IR Setup',
    tab_cost: 'Cost Analysis',
    tab_flow: 'Capital Flow',
    updateData: '⟳ Refresh',
    updatingData: '⟳ Refreshing...',
    fetchingCcass: 'Fetching CCASS data',
    noCcassData: 'No CCASS data available',
    firstLoadHint: 'First load takes 2-5 minutes (90 trading days). Subsequent updates take only a few minutes.',
    marketDataReady: 'Market data is ready. You can check the "Market" tab first.',
    startFetch: 'Start Fetching CCASS Data',
    fetchFailed: 'Data fetch failed',

    // Overview / KPI
    ccassHoldings: 'CCASS Holdings',
    ofTotalShares: 'of issued shares',
    southboundHoldings: 'Southbound Holdings',
    signalScore: 'Signal Score',
    noSignals: 'No signals',
    issuedShares: 'Issued Shares',
    latestAnomalies: '🚨 Latest Signals',
    high: 'High',
    medium: 'Medium',
    top10Holders: '🏦 Top 10 Participants',
    seat: 'Participant',
    holdings: 'Holdings',
    pct: '% of Issued',
    pctNotDisclosed: 'HKEX omitted % of issued units. The last published unit count lags daily creation/redemption, so the percentage column is left blank. Share counts are still valid.',
    irEnabled: '🔒 IR Tracking Enabled',
    irEnabledDesc: 'Attributed shareholder seats cover {shares} shares. Switch to "IR Tracking" tab to view selloff data.',
    irDisabled: '🔓 Enable IR Tracking',
    irDisabledDesc: 'Upload a shareholder register/broker account file to unlock shareholder selloff tracking, unlock calendar, attribution analysis and other IR-specific features. Supports Excel, CSV, PDF formats.',
    irDisabledNote: 'No account needed. Data is saved entirely locally. Switch to the "IR Setup" tab to upload files.',

    // Price card
    open: 'Open',
    priceHigh: 'High',
    priceLow: 'Low',
    volume: 'Vol',
    turnover: 'Turnover',
    avgVol30d: '30d Avg Vol',
    range: 'Range',

    // CCASS tab
    southboundDailyNet: 'Southbound Daily Net Buy/Sell (10K shares)',
    ccassRange: 'Range',
    ccassBackfillShort: 'Stored history is short; backfilling now',
    ccassHoldingsTrend: 'CCASS Holdings Trend (10K shares)',
    ccassDailyTable: 'CCASS Daily Summary',
    participantHoldings: 'Participant Holdings',
    exportExcel: '📥 Export Excel',
    exporting: 'Exporting...',
    latestDate: 'Latest Date',
    showChanges: 'With Changes',
    showAll: 'All',
    dailyChange: '1D Chg',
    change3d: '3D Chg',
    attribution: 'Attribution',
    netBuy: 'Net Buy',

    // Trading tab
    priceChart6m: 'Price Trend (Last 6 Months)',
    close: 'Close',
    volume_short: 'Volume',
    announcements: '📢 Corporate Announcements',
    fetchAnnouncements: '🔄 Fetch Announcements',
    fetchingAnn: 'Fetching...',
    noAnnouncements: 'No announcement data. Click "Fetch Announcements" to retrieve from HKEX.',
    tradingDetails: 'Trading Details',
    date: 'Date',
    change: 'Change',
    // Announcement categories
    cat_earnings: 'Earnings',
    cat_buyback: 'Buyback',
    cat_trade: 'Shareholding Change',
    cat_placing: 'Placing/Financing',
    cat_director: 'Director Change',
    cat_material: 'Material Event',
    cat_dividend: 'Dividend',
    cat_other: 'Other',

    // Signals tab
    signalScoreLabel: 'Anomaly Signal Score',
    sellSignals: '📉 Sell Signals',
    buySignals: '📈 Buy Signals',
    strongAccumulation: 'Strong Accumulation',
    neutral: 'Neutral',
    strongDistribution: 'Strong Distribution',
    detectedAnomalies: '🔍 Detected Anomalies',
    highPriority: 'High Priority',
    midPriority: 'Medium Priority',
    noAnomalies: 'No significant anomalies detected',
    signalExplanation: '📖 Signal Guide',
    scoreLogic: '<strong>Scoring Logic:</strong> Combines single-day participant changes, 3-day trends, new/exiting participants, Southbound flows, and volume anomalies into a 0-100 score.',
    highPriorityAnomaly: '<strong>High Priority:</strong> Single participant daily change exceeds 1% of issued shares, or 3-day cumulative change exceeds 1%.',
    newParticipant: '<strong>New Participant:</strong> First appearance in CCASS holdings with >0.2% — may indicate new institution building a position or shareholder seat transfer.',
    participantExit: '<strong>Participant Exit:</strong> Held >0.3% on previous day but fully exited — may indicate completed selling.',
    volumeAnomaly: '<strong>Volume Spike:</strong> Volume exceeds 2x of 30-day average; price direction indicates buy/sell pressure.',
    signalDisclaimer: '⚠️ Signals are for reference only and do not constitute investment advice. CCASS data is T+2.',

    // IR tab
    uploadTitle: '📤 Upload Shareholder Register / Broker Account',
    uploadDesc: 'Supports Excel (.xlsx), CSV and PDF. Put the header in the first 30 rows, with a shareholder name column (姓名 / 股東名稱) and a shares column. Participant ID is optional. Recognized rows appear in the Shareholder Attribution table below and stay editable. A later upload does not overwrite rows you edited here unless you confirm.',
    selectFile: 'Select File to Upload',
    uploadingParsing: 'Uploading & Parsing...',
    addManualAttr: '+ Add Attribution Manually',
    parseSuccess: '✅ Parse Successful',
    parseFailed: '❌ Parse Failed',
    parseSummary: 'Identified {count} shareholder-seat attributions, {unlocks} unlock records.',
    manualAddTitle: 'Add Shareholder-Seat Attribution',
    shareholderName: 'Shareholder Name',
    participantId: 'Participant ID',
    participantName: 'Participant Name (e.g. Goldman Sachs)',
    sharesBaseline: 'Baseline Shares',
    costHkd: 'Cost (HKD)',
    investorType: 'Investor Type (e.g. Pre-IPO/Cornerstone/Management)',
    unlockDate: 'Unlock Date (YYYY-MM-DD)',
    notes: 'Notes',
    edit: 'Edit',
    delete: 'Delete',
    saveChanges: 'Save',
    holdingPct: 'Holding %',
    editShareholder: 'Edit shareholder',
    deleteShareholderTitle: 'Delete this shareholder?',
    deleteShareholderBody: 'This removes “{name}” ({seat}). Selloff and unlock rows that belong only to this shareholder are removed too.',
    confirmDelete: 'Delete',
    importKept: '{count} rows were edited in the panel and were not overwritten. Notes stay. Shares, cost, and unlock date are replaced only after you confirm.',
    overwriteManual: 'Overwrite these with the file',
    overwriteDone: 'Replaced {count} edited rows with the file. Notes were kept.',
    totalRow: 'Total',
    actions: 'Actions',
    editedBadge: 'Edited',
    pctHint: 'Issued shares: {issued}. Changing shares or the percentage updates the other.',
    pctHintNoIssued: 'Issued shares are unknown, so the percentage is stored as typed and does not change the share count.',
    noAttribution: 'No shareholder attribution configured. Upload a file or add manually to enable selloff tracking.',
    shareholderAttribution: '👥 Shareholder-Seat Attribution',
    shareholder: 'Shareholder',
    currentHoldings: 'Current Holdings',
    baselineHoldings: 'Baseline',
    changeFromBaseline: 'vs Baseline',
    change30d: '30D Change',
    cost: 'Cost',
    selloffTracking: '📉 Daily Selloff Tracking',
    closingPrice: 'Close',
    unlockCalendar: '📅 Unlock Calendar',
    upcomingUnlocks: 'Upcoming',
    pastUnlocks: 'Past',

    // Reports tab
    autoGenerateReport: '📝 Generate Report',
    reportType: 'Report Type',
    daily: 'Daily',
    weekly: 'Weekly',
    monthly: 'Monthly',
    reportMode: 'Mode',
    traderView: '📈 Trader View',
    irView: '🏢 IR View',
    publicData: 'Public data',
    inclSelloff: 'With selloff data',
    generateReport: '⚡ Generate',
    generating: 'Generating...',
    generateFailed: 'Generation failed',
    irModeHint: 'IR reports include shareholder attribution, selloff tracking, and unlock calendar. Requires shareholder register upload in IR Setup.',
    traderModeHint: 'Trader reports are based on public market data, CCASS, Southbound Connect, announcements and anomaly signals only.',
    webVersion: '🌐 Web View',
    downloadMd: '📥 Download Markdown',
    aiEnhanced: 'AI Enhanced',
    template: 'Template',
    aiEnhancedHint: 'AI Enhanced uses LLM for deeper analysis (requires API configuration in Settings)',
    reportPeriod: 'Report Period',
    generatedAt: 'Generated at',

    // Settings
    settingsTitle: '⚙️ Settings',
    outOfBox: '🔓 Works Out of the Box',
    outOfBoxDesc: 'This tool uses public data sources (HKEX, Tencent Finance, Yahoo Finance) by default. <strong>No API keys are required for core functionality.</strong> Below are optional advanced configurations for enhanced features.',
    servicePort: '🌐 Service Port',
    listenPort: 'Listen Port',
    portRestart: 'Restart required after change. Current access: http://localhost:{port}',
    aiReport: '🤖 AI-Enhanced Reports (Optional)',
    aiReportDesc: 'When enabled, daily/weekly/monthly reports will use LLM to generate deeper analysis sections',
    enable: 'Enable',
    apiProvider: 'API Provider',
    apiBaseUrl: 'API Base URL',
    apiKey: 'API Key',
    apiKeyLocal: 'API keys are saved only in local config.json and never uploaded',
    modelName: 'Model Name',
    provider_openai: 'OpenAI (GPT-4o)',
    provider_anthropic: 'Anthropic (Claude)',
    provider_deepseek: 'DeepSeek',
    provider_custom: 'Custom (OpenAI-compatible)',
    notifications: '🔔 Anomaly Notifications (Optional)',
    notificationsDesc: 'Push major selloff, new institution events via Webhook',
    webhookUrl: 'Webhook URL (Feishu/Lark/Slack)',
    companyInfo: '🏢 IR Report Company Info (Optional)',
    companyInfoDesc: 'Fill in to display company information in IR reports',
    companyName: 'Company Name',
    irEmail: 'IR Contact Email',
    logoUrl: 'Logo URL (Optional)',
    configFileLocation: 'Config file: <code className="bg-slate-100 px-1 rounded">backend/config.json</code>',
    allDataLocal: 'All data is stored locally and will never be uploaded to any cloud service',

    // Auto fetch
    autoFetch: '⏰ Daily Auto Update / 每日自动更新',
    autoFetchDesc: 'When your computer is on and this app is running, automatically fetch the latest trading and CCASS data for all tracked stocks at the specified time each day',
    autoFetchTime: 'Daily update time',
    autoFetchDays: 'Lookback days',
    autoFetchLastRun: 'Last run',
    autoFetchNextRun: 'Next run',
    autoFetchNever: 'Never',
    autoFetchRunNow: '▶ Manual update all',
    autoFetchRunning: 'Running…',
    autoFetchRunNowHint: 'Immediately fetch latest data for all tracked stocks',
    autoFetchDisabledHint: 'When enabled, data will auto-refresh daily while this app is running. You can also click "Update Data" any time.',
    autoFetchTimeHint: '24h format (e.g. 17:30). Best set 15+ minutes after HK market close (16:00) to wait for HKEX updates.',
    autoFetchDaysHint: 'Backfill window (days) when fetching.',

    // Cost Analysis
    costAnalysis: 'Cost Structure & Selling Pressure',
    currentPrice: 'Current Price',
    totalAnalyzedShares: 'Analyzed Holdings',
    pressureScore: 'Pressure Score',
    pressureLow: 'Low Pressure',
    pressureModerate: 'Moderate Pressure',
    pressureHigh: 'High Pressure',
    pressureExtreme: 'Extreme Pressure',
    costDistribution: 'Cost Distribution',
    deepUnderwater: 'Deep Underwater',
    underwater: 'Underwater',
    atCost: 'Near Cost',
    moderateProfit: 'Moderate Profit',
    goodProfit: 'Good Profit',
    heavyProfit: 'Heavy Profit',
    shareholderCostChart: 'Shareholder Cost Distribution',
    costPrice: 'Cost (HKD)',
    shareholderList: 'Shareholder Details',
    profitPct: 'P/L %',
    floatingPnL: 'Floating P/L',
    pressureLevel: 'Pressure',
    broker: 'Broker/Participant',
    investorTypePie: 'Investor Type Distribution',
    brokerConcentration: 'Broker Concentration',
    shareholderCount: 'Shareholders',
    totalShares: 'Total Shares',
    pressureHeavy: 'Heavy',
    pressureModerateLevel: 'Moderate',
    pressureLowLevel: 'Light',
    pressureUnderwater: 'Underwater',
    pressureUnknown: 'Unknown',
    noCostData: 'No cost data available',
    noAttributionData: 'No shareholder attribution data configured. Please upload a shareholder register in IR Setup to enable cost analysis.',

    // Capital Flow
    capitalFlow: 'Capital Flow Analysis',
    topInflows: 'Top 10 Net Buying',
    topOutflows: 'Top 10 Net Selling',
    netChange: 'Net Change',
    currentShares: 'Current Holdings',
    attributedShareholder: 'Attributed Shareholder',
    dailyBrokerFlows: 'Daily Net Buy/Sell by Top 10 Brokers',
    shareholderFlow: 'Known Shareholder Flows',
    periodRange: 'Period',
    periodEnd: 'End date',
    unknownRetail: 'Retail/Unknown',
    buying: 'Buying',
    selling: 'Selling',
    noFlowData: 'No capital flow data available',

    // Capital flow additions
    dailyNetFlow: 'Daily Net Flow (Aggregate)',
    dailyNetFlowHint: 'Sum of net changes for all tracked brokers (10K shares). Positive = net inflow, negative = net outflow',
    tenKUnit: 'K',
    tenKShares: '10K sh',
    positiveHint: 'Positive = net buying, negative = net selling',

    // Cost analysis
    tenKSharesShort: '10K sh',
    daysUnit: 'd',
    scenarioFootnote: '* Scenario estimates are based on cost structure, unlock schedules and historical volume; actual selling pressure is affected by sentiment and macro conditions.',
  },
} as const

type TranslationKey = keyof typeof translations.zh

type TParams = Record<string, string | number | boolean> & {
  returnObjects?: boolean
}

interface I18nContextType {
  t: (key: string, params?: TParams) => any
  lang: Lang
  setLang: (l: Lang) => void
  brokerName: (pid: string | undefined | null, zhName?: string | null) => string
  stockName: (code: string, zhName?: string) => string
}

const I18nContext = createContext<I18nContextType>({
  t: (key: string) => key,
  lang: 'zh',
  setLang: () => {},
  brokerName: (_pid, zhName) => zhName || '',
  stockName: (_code, zhName) => zhName || '',
})

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(() => {
    if (typeof window !== 'undefined') {
      const saved = localStorage.getItem('hk-panel-lang')
      if (saved === 'zh' || saved === 'en') return saved
    }
    return 'zh'
  })

  useEffect(() => {
    localStorage.setItem('hk-panel-lang', lang)
    document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en-US'
  }, [lang])

  const setLang = (l: Lang) => setLangState(l)

  const t = (key: string, params?: TParams): any => {
    const returnObjects = params?.returnObjects ?? false
    const dict: any = translations[lang]
    // Support nested keys like 'feature_ccass.title'
    const parts = key.split('.')
    let val: any = dict
    for (const p of parts) {
      if (val && typeof val === 'object' && p in val) {
        val = val[p]
      } else {
        val = null
        break
      }
    }
    if (val == null) {
      // Fallback to Chinese
      let fallback: any = translations.zh
      for (const p of parts) {
        if (fallback && typeof fallback === 'object' && p in fallback) {
          fallback = fallback[p]
        } else {
          fallback = key
          break
        }
      }
      val = fallback
    }
    if (returnObjects && typeof val === 'object' && Array.isArray(val)) {
      return val
    }
    let text = typeof val === 'string' ? val : key
    if (params) {
      for (const [k, v] of Object.entries(params)) {
        if (k === 'returnObjects') continue
        text = text.replace(`{${k}}`, String(v))
      }
    }
    return text
  }

  const brokerName = (pid: string | undefined | null, zhName?: string | null): string => {
    if (!pid) return zhName || ''
    if (lang === 'en' && BROKER_NAME_EN[pid]) return BROKER_NAME_EN[pid]
    return zhName || pid
  }

  const stockName = (code: string, zhName?: string): string => {
    if (lang === 'en') return STOCK_NAME_EN[code] || zhName || code
    return zhName || code
  }

  return (
    <I18nContext.Provider value={{ t, lang, setLang, brokerName, stockName }}>
      {children}
    </I18nContext.Provider>
  )
}

export function useT() {
  return useContext(I18nContext)
}
