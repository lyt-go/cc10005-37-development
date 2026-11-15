import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/captcharesult'
const columns = [
  "结果编号",
  "策略编号",
  "验证码类型",
  "识别服务商",
  "实例编号",
  "目标站点",
  "识别结果",
  "连续失败数",
  "失败原因",
  "上报单号",
  "上报时间",
  "关联分配",
  "处置说明"
]
const reportFields = [
  "策略编号",
  "验证码类型",
  "识别服务商",
  "实例编号",
  "目标站点",
  "识别结果",
  "失败原因",
  "上报单号"
]
const emptyReport: Record<string, string> = {
  "策略编号": "",
  "验证码类型": "",
  "识别服务商": "",
  "实例编号": "",
  "目标站点": "",
  "识别结果": "识别失败",
  "失败原因": "",
  "上报单号": ""
}

type Row = Record<string, string | number | null>

export default function CaptcharesultView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [report, setReport] = useState<Record<string, string>>(emptyReport)
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('验证码识别结果列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '验证码识别结果列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  async function submitReport(event: FormEvent) {
    event.preventDefault()
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(ENDPOINT, {
        method: 'POST',
        body: JSON.stringify({ values: report }),
      })
      const payload = await response.json()
      if (!response.ok || !payload.ok) {
        setErrorMessage(payload.message ?? '识别结果上报未生效，请检查填写内容')
        return
      }
      setNotice(payload.message ?? '识别结果已上报')
      setReport(emptyReport)
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '识别结果上报失败')
    }
  }

  const manualCount = rows.filter((row) => row.status === '人工打码中').length
  const failureCount = rows.filter((row) => row['识别结果'] === '识别失败').length
  const stats = [
    { label: '识别结果总数', value: total },
    { label: '失败记录', value: failureCount },
    { label: '人工打码中', value: manualCount },
  ]

  return (
    <section className="page" data-module="captcharesult">
      <header className="page-head">
        <div>
          <h2>验证码识别结果</h2>
          <p className="page-desc">按验证码类型、识别服务商、代理实例与目标站点汇总连续失败，达到阈值自动隔离代理分配并转入人工打码。</p>
        </div>
        <div className="page-actions">
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出识别结果清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        {stats.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
      </div>

      <form className="filter-bar" onSubmit={submitReport}>
        {reportFields.map((field) => (
          <label key={field} className="filter-item">
            <span>{field}</span>
            <input
              value={report[field] ?? ''}
              placeholder={`填写${field}`}
              onChange={(event) => setReport((prev) => ({ ...prev, [field]: event.target.value }))}
            />
          </label>
        ))}
        <button className="btn primary" type="submit">上报识别结果</button>
      </form>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        {filterFields.map((field) => (
          <label key={field} className="filter-item">
            <span>{field}</span>
            <input
              value={filters[field] ?? ''}
              placeholder={`按${field}检索`}
              onChange={(event) => setFilters((prev) => ({ ...prev, [field]: event.target.value }))}
            />
          </label>
        ))}
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => setFilters({})}>重置条件</button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>状态</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
              {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
              <td>{String(row.status ?? '—')}</td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 1}>暂无验证码识别结果，可先上报识别结果</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条验证码识别结果记录</span>
        {notice && <span className="notice-text">{notice}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
