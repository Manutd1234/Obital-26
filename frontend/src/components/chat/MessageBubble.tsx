import { useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import rehypeKatex from 'rehype-katex'
import remarkMath from 'remark-math'
import { parseQuizData, stripQuizTags } from '../../lib/utils'
import type { ChatMessage } from '../../api/chat'
import { QuizWidget } from './QuizWidget'
import { ToolCallCard } from './ToolCallCard'

interface Props {
 message: ChatMessage
 isStreaming?: boolean
}

export function MessageBubble({ message, isStreaming }: Props) {
 const isUser = message.role === 'user'

 const quizData = useMemo(() => {
 if (isUser || isStreaming) return null
 return parseQuizData(message.content)
 }, [message.content, isUser, isStreaming])

 const cleanedContent = useMemo(() => {
 if (isUser) return message.content
 if (isStreaming) return message.content
 return stripQuizTags(message.content)
 }, [message.content, isUser, isStreaming])

 const toolCalls: string[] = useMemo(() => {
 if (isStreaming) return []
 if (message.activity?.length) return []
 const matches = cleanedContent.match(/\[tool\]\s*(.*)/g)
 return (matches ?? []) as string[]
 }, [cleanedContent, isStreaming, message.activity])

 const contentWithoutTools = useMemo(() => {
 if (isStreaming) return cleanedContent
 return cleanedContent.replace(/\[tool\]\s*.*/g, '').trim()
 }, [cleanedContent, isStreaming])

 return (
 <div className={`flex ${isUser ? 'justify-end' : 'justify-start'} ${isUser ? 'animate-slide-left' : 'animate-slide-right'}`}>
 <div className={`${isUser ? 'max-w-[72%]' : 'max-w-[85%] space-y-3'}`}>
 {!isUser && message.activity && message.activity.length > 0 && (
 <div className="space-y-2">
 {message.activity.map((item) => (
 <ToolCallCard key={item.id} item={item} />
 ))}
 </div>
 )}

 {/* Message bubble */}
 {(isUser || contentWithoutTools || isStreaming) && (
 <div
 className={`px-5 py-3 ${
 isUser
 ? 'rounded-full bg-surface-card text-text-primary shadow-sm'
 : 'text-text-secondary'
 }`}
 >
 {isUser ? (
 <p className="text-sm leading-relaxed">{message.content}</p>
 ) : contentWithoutTools ? (
 <div className="prose-custom text-sm leading-relaxed">
 <ReactMarkdown remarkPlugins={[remarkMath]} rehypePlugins={[rehypeKatex]}>
 {contentWithoutTools}
 </ReactMarkdown>
 </div>
 ) : (
 <p className="text-sm leading-relaxed text-accent-muted">Working...</p>
 )}
 {isStreaming && (
 <span className="inline-block w-0.5 h-4 bg-accent ml-0.5 animate-pulse" />
 )}
 </div>
 )}

 {/* Legacy tool calls */}
 {!message.activity?.length && toolCalls.map((tc, i) => (
 <ToolCallCard key={i} content={tc.replace('[tool] ', '')} />
 ))}

 {/* Quiz widget */}
 {quizData != null && <QuizWidget data={quizData} />}
 </div>
 </div>
 )
}
