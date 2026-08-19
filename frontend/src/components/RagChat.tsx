import { useEffect, useRef, useState, type FormEvent } from 'react'

import { call } from '../lib/auth'

interface RagNode {
  type: string
  key: string
  label: string
  summary: string
  score: number | null
}

interface RagResponse {
  question: string
  nodes: RagNode[]
  answer: string | null
  generation_error?: string
}

interface Message {
  id: number
  question: string
  answer: string | null
  nodes: RagNode[]
  error: string | null
  loading: boolean
}

const EXAMPLES = ['대전역은 어떤 노선이 지나가?', '부산행 KTX 중 지연된 열차는?']

let nextId = 0

/** Floating GraphRAG query bar: docked to the bottom of the map, collapsed to
 * a single input pill until the first question is asked. Keeps only the
 * generated answer up front -- the retrieved nodes back it up but stay
 * behind a "근거 보기" toggle so the panel doesn't turn into a data dump. */
export default function RagChat() {
  const [open, setOpen] = useState(false)
  const [input, setInput] = useState('')
  const [messages, setMessages] = useState<Message[]>([])
  const [shownSources, setShownSources] = useState<Set<number>>(new Set())
  const listRef = useRef<HTMLDivElement>(null)
  const busy = messages.some((m) => m.loading)

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages])

  const ask = async (question: string) => {
    const q = question.trim()
    if (!q || busy) return
    setInput('')
    setOpen(true)
    const id = nextId++
    setMessages((prev) => [...prev, { id, question: q, answer: null, nodes: [], error: null, loading: true }])
    try {
      const res = await call<RagResponse>('/api/graphrag/query', {
        method: 'POST',
        body: JSON.stringify({ query: q, generate: true }),
      })
      setMessages((prev) =>
        prev.map((m) => (m.id === id ? { ...m, loading: false, answer: res.answer, nodes: res.nodes } : m)),
      )
    } catch (err) {
      setMessages((prev) =>
        prev.map((m) =>
          m.id === id
            ? { ...m, loading: false, error: err instanceof Error ? err.message : '질의에 실패했습니다' }
            : m,
        ),
      )
    }
  }

  const submit = (e: FormEvent) => {
    e.preventDefault()
    void ask(input)
  }

  const toggleSources = (id: number) => {
    setShownSources((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  return (
    <div className={`rag-chat ${open ? 'open' : ''}`}>
      {open && (
        <>
          <div className="rag-chat-head">
            <span>🕊️ 비둘기 역장님에게 물어보기</span>
            <button type="button" className="rag-chat-collapse" onClick={() => setOpen(false)} aria-label="접기">
              ⌄
            </button>
          </div>
          <div className="rag-chat-list" ref={listRef}>
            {messages.length === 0 && (
              <div className="rag-chat-empty">
                <p>예를 들어 이렇게 물어보세요</p>
                <div className="rag-chat-examples">
                  {EXAMPLES.map((ex) => (
                    <button type="button" key={ex} onClick={() => void ask(ex)}>{ex}</button>
                  ))}
                </div>
              </div>
            )}
            {messages.map((m) => (
              <div className="rag-msg" key={m.id}>
                <div className="rag-msg-q">{m.question}</div>
                <div className="rag-msg-a">
                  {m.loading && <span className="rag-loading">역 자료를 뒤지는 중…</span>}
                  {m.error && <span className="rag-error">{m.error}</span>}
                  {!m.loading && !m.error && (
                    <>
                      <p>{m.answer ?? '관련 정보를 찾지 못했어요.'}</p>
                      {m.nodes.length > 0 && (
                        <div className="rag-sources">
                          <button type="button" className="rag-sources-toggle" onClick={() => toggleSources(m.id)}>
                            근거 {m.nodes.length}개 {shownSources.has(m.id) ? '접기' : '보기'}
                          </button>
                          {shownSources.has(m.id) && (
                            <ul>
                              {m.nodes.slice(0, 5).map((n) => (
                                <li key={`${n.type}-${n.key}`}>
                                  <span className="rag-source-type">{n.type}</span>
                                  {n.summary}
                                </li>
                              ))}
                            </ul>
                          )}
                        </div>
                      )}
                    </>
                  )}
                </div>
              </div>
            ))}
          </div>
        </>
      )}
      <form className="rag-chat-bar" onSubmit={submit}>
        <input
          placeholder="비둘기 역장님에게 물어보세요"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onFocus={() => setOpen(true)}
        />
        <button type="submit" disabled={busy || !input.trim()}>{busy ? '…' : '물어보기'}</button>
      </form>
    </div>
  )
}
