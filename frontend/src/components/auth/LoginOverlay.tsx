import React, { useState } from 'react'
import { useAuthStore } from '../../stores/authStore'
import { useDocStore } from '../../stores/docStore'
import { useLessonStore } from '../../stores/lessonStore'
import { useUIStore } from '../../stores/uiStore'
import { accountExists, bootstrap, login, register } from '../../api/auth'

type AuthStep = 'username' | 'pin' | 'pin_confirm' | 'passphrase'

function normalizeUsername(value: string): string {
 return value.trim().toLowerCase()
}

function validateUsername(value: string): string | null {
 const username = normalizeUsername(value)
 if (!username) return 'Enter a username.'
 if (!/^[a-z0-9][a-z0-9_-]{1,63}$/.test(username)) {
 return 'Username must be 2-64 characters, start with a letter or number, and use only lowercase letters, numbers, underscores, or hyphens.'
 }
 return null
}

function validatePin(value: string): string | null {
 const pin = value.trim()
 if (!pin) return 'Enter a PIN.'
 if (!/^\d+$/.test(pin)) return 'PIN must use digits only.'
 if (pin.length < 4 || pin.length > 12) return 'PIN must be 4-12 digits.'
 return null
}

function apiErrorMessage(error: unknown, fallback: string): string {
 const message = error instanceof Error ? error.message : ''
 if (!message) return fallback

 if (message.includes('API 401')) {
 if (fallback.toLowerCase().includes('pin')) return 'Invalid PIN.'
 if (fallback.toLowerCase().includes('passphrase') || fallback.toLowerCase().includes('unlock')) return 'Invalid vault passkey.'
 return fallback
 }
 if (message.includes('API 422')) {
 return 'Check the format and try again. Usernames must use lowercase letters, numbers, underscores, or hyphens. PINs must be 4-12 digits.'
 }
 if (message.includes('API 429')) {
 return 'Too many failed attempts. Try again later.'
 }

 const detailMatch = message.match(/"detail"\s*:\s*(".*?"|\[.*\])/)
 if (detailMatch?.[1]) {
 try {
 const detail = JSON.parse(detailMatch[1])
 if (typeof detail === 'string') return detail
 if (Array.isArray(detail)) {
 const firstMessage = detail
 .map((item) => {
 if (!item || typeof item !== 'object') return ''
 const entry = item as { msg?: unknown; loc?: unknown[] }
 const msg = typeof entry.msg === 'string' ? entry.msg.replace(/^Value error,\s*/i, '') : ''
 const field = Array.isArray(entry.loc) ? String(entry.loc[entry.loc.length - 1] || '') : ''
 if (field === 'pin') return 'PIN must be 4-12 digits.'
 if (field === 'account_id' || field === 'username') return 'Username must use lowercase letters, numbers, underscores, or hyphens.'
 return msg
 })
 .find(Boolean)
 if (firstMessage) return firstMessage
 }
 } catch {
 // Fall through to the raw message.
 }
 }

 return message
}

export function LoginOverlay() {
 const [step, setStep] = useState<AuthStep>('username')
 const [usernameInput, setUsernameInput] = useState('')
 const [pinInput, setPinInput] = useState('')
 const [confirmPinInput, setConfirmPinInput] = useState('')
 const [passphraseInput, setPassphraseInput] = useState('')
 const [error, setError] = useState('')
 const [loading, setLoading] = useState(false)
 const [isRegister, setIsRegister] = useState(false)
 const theme = useUIStore((s) => s.theme)
 const toggleTheme = useUIStore((s) => s.toggleTheme)
 const setDocs = useDocStore((s) => s.setDocs)
 const setLessons = useLessonStore((s) => s.setLessons)

 const {
 username,
 setUsername,
 unlock: storeUnlock,
 } = useAuthStore()

 const switchToLogin = () => {
 setIsRegister(false)
 setStep('username')
 setError('')
 setPinInput('')
 setConfirmPinInput('')
 setPassphraseInput('')
 }

 const switchToRegister = () => {
 setIsRegister(true)
 setStep('username')
 setError('')
 setPinInput('')
 setConfirmPinInput('')
 setPassphraseInput('')
 }

 const handleUsername = async () => {
 if (!usernameInput.trim()) return
 const uname = normalizeUsername(usernameInput)
 const validationError = validateUsername(uname)
 if (validationError) {
 setError(validationError)
 return
 }
 setLoading(true)
 setError('')
 try {
 const existsResult = await accountExists(uname)
 if (!isRegister && !existsResult.exists) {
 setError('No account found with that name. Create an account first, then log in.')
 setLoading(false)
 return
 }
 if (isRegister && existsResult.exists) {
 setError('Username already exists. Choose another name or go back to login.')
 setLoading(false)
 return
 }
 setUsernameInput(uname)
 setUsername(uname)
 setStep('pin')
 } catch (e: unknown) {
 setError(apiErrorMessage(e, 'Could not check that account. Try again.'))
 } finally {
 setLoading(false)
 }
 }

 const handlePin = async () => {
 if (isRegister && step === 'pin') {
 if (!pinInput.trim()) return
 const validationError = validatePin(pinInput)
 if (validationError) {
 setError(validationError)
 return
 }
 setError('')
 setStep('pin_confirm')
 return
 }

 const currentPin = step === 'pin_confirm' ? confirmPinInput : pinInput
 if (!currentPin.trim()) return
 const validationError = validatePin(currentPin)
 if (validationError) {
 setError(validationError)
 return
 }
 setLoading(true)
 setError('')

 if (isRegister) {
 if (pinInput !== confirmPinInput) {
 setError('PINs do not match')
 setPinInput('')
 setConfirmPinInput('')
 setStep('pin')
 setLoading(false)
 return
 }
 try {
 await register(username, pinInput)
 setStep('passphrase')
 } catch (e: unknown) {
 setError(apiErrorMessage(e, 'Registration failed'))
 }
 } else {
 try {
 await login(username, pinInput)
 setStep('passphrase')
 } catch (e: unknown) {
 const message = e instanceof Error ? e.message : ''
 if (message.includes('API 404')) {
 setStep('username')
 setPinInput('')
 setError('No account found with that name. Create an account first, then log in.')
 } else {
 setError(apiErrorMessage(e, 'Invalid PIN'))
 }
 }
 }
 setLoading(false)
 }

 const handlePassphrase = async () => {
 if (!passphraseInput.trim()) return
 setLoading(true)
 setError('')

 try {
 const res = await bootstrap(passphraseInput)
 if (res.lessons && res.lessons.length > 0) {
 setLessons((res.lessons || []).map((lesson: any) => ({
 lesson_id: lesson.id,
 title: lesson.title,
 topic: lesson.objective,
 status: lesson.status,
 current_checkpoint: lesson.current_checkpoint,
 total_checkpoints: lesson.total_checkpoints,
 checkpoints: lesson.checkpoints || [],
 })))
 }
 if (res.docs && res.docs.length > 0) {
 setDocs((res.docs || []).map((doc: any) => ({
 doc_id: doc.id,
 name: doc.name,
 chunk_count: doc.chunk_count ?? doc.chunks?.length ?? 0,
 uploaded_at: doc.uploaded_at,
 })))
 }
 storeUnlock(res.token || '')
 } catch (e: unknown) {
 setError(apiErrorMessage(e, 'Invalid vault passkey'))
 }
 setLoading(false)
 }

 const handleKeyDown = (e: React.KeyboardEvent) => {
 if (e.key !== 'Enter') return
 e.preventDefault()
 if (step === 'username') handleUsername()
 else if (step === 'pin' || step === 'pin_confirm') handlePin()
 else if (step === 'passphrase') handlePassphrase()
 }

 const promptLabel =
 step === 'username'
 ? isRegister ? 'Choose username' : 'Username'
 : step === 'pin'
 ? isRegister ? 'Create PIN' : 'PIN'
 : step === 'pin_confirm'
 ? 'Confirm PIN'
 : 'Vault passkey'

 const helperText =
 step === 'username'
 ? isRegister ? 'Pick the account name you will use to log in.' : 'Enter your existing account name.'
 : step === 'pin'
 ? isRegister ? 'Your sign-in PIN identifies you. You will create a separate encryption passkey next.' : 'Enter your sign-in PIN. Your private workspace passkey is requested on the next step.'
 : step === 'pin_confirm'
 ? 'Confirm the PIN so we know it matches.'
 : isRegister ? 'Create the encryption passkey for your one private workspace. It protects your study data and is needed on every device.' : 'This is not your sign-in PIN. Enter the encryption passkey for your one private workspace.'

 const buttonLabel =
 step === 'username'
 ? isRegister ? 'Start Account Setup' : 'Log In'
 : step === 'pin'
 ? isRegister ? 'Continue' : 'Continue'
 : step === 'pin_confirm'
 ? 'Create Account'
 : 'Unlock Obital'

 const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
 const val = e.target.value
 if (step === 'username') setUsernameInput(val)
 else if (step === 'pin') {
 if (/\D/.test(val)) setError('PIN must use digits only.')
 else if (error === 'PIN must use digits only.') setError('')
 setPinInput(val.replace(/\D/g, '').slice(0, 12))
 }
 else if (step === 'pin_confirm') {
 if (/\D/.test(val)) setError('PIN must use digits only.')
 else if (error === 'PIN must use digits only.') setError('')
 setConfirmPinInput(val.replace(/\D/g, '').slice(0, 12))
 }
 else if (step === 'passphrase') setPassphraseInput(val)
 }

 const inputValue =
 step === 'username' ? usernameInput :
 step === 'pin' ? pinInput :
 step === 'pin_confirm' ? confirmPinInput :
 passphraseInput

 return (
 <div className="fixed inset-0 z-50 bg-surface flex items-center justify-center p-6">
 <button
 onClick={toggleTheme}
 className="absolute right-5 top-5 flex h-10 w-10 items-center justify-center rounded-xl bg-surface-card text-accent-muted transition-all hover:text-text-primary hover:bg-surface-lighter"
 title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
 >
 <span className="material-symbols-outlined text-lg">
 {theme === 'dark' ? 'light_mode' : 'dark_mode'}
 </span>
 </button>
 <div className="w-full max-w-[420px] animate-fade-in">
 <div className="flex flex-col items-center text-center mb-8">
 <div className="w-12 h-12 bg-surface-card rounded-xl flex items-center justify-center text-accent mb-5">
 <span className="material-symbols-outlined text-2xl">school</span>
 </div>
 <h1 className="font-semibold text-3xl text-text-primary tracking-tight">
 {isRegister ? 'Create your Obital account' : 'Log in to Obital'}
 </h1>
 <p className="text-sm text-accent-muted mt-3">
 {isRegister ? 'Set up your account here.' : 'Use your account name and PIN to continue.'}
 </p>
 </div>

 <div className="bg-surface rounded-2xl p-6 shadow-sm flex flex-col gap-5">
 <div>
 <label className="block text-sm font-medium text-text-primary mb-2">
 {promptLabel}
 </label>
 <input
 type={step === 'username' ? 'text' : 'password'}
 inputMode={step === 'pin' || step === 'pin_confirm' ? 'numeric' : undefined}
 pattern={step === 'pin' || step === 'pin_confirm' ? '[0-9]*' : undefined}
 maxLength={step === 'pin' || step === 'pin_confirm' ? 12 : undefined}
 autoCapitalize={step === 'username' ? 'none' : undefined}
 autoCorrect={step === 'username' ? 'off' : undefined}
 className="w-full rounded-xl bg-surface px-4 py-3 text-base text-text-primary outline-none transition-all placeholder-accent-muted/40 focus:border-accent focus:ring-2 focus:ring-accent/10"
 value={inputValue}
 onChange={handleInputChange}
 onKeyDown={handleKeyDown}
 disabled={loading}
 autoFocus
 aria-label={promptLabel}
 placeholder={promptLabel}
 />
 <p className="text-xs text-accent-muted/60 mt-2">{helperText}</p>
 </div>

 {step === 'passphrase' && (
 <div className="rounded-xl bg-surface-card px-4 py-3 text-left">
 <div className="flex items-start gap-3">
 <span className="material-symbols-outlined mt-0.5 text-base text-accent">encrypted</span>
 <div>
 <p className="text-xs font-medium text-text-primary">Why two credentials?</p>
 <p className="mt-1 text-xs leading-relaxed text-accent-muted">
 Your PIN signs in to your account. Your workspace passkey decrypts your notes, chats, and progress so the server cannot read them. Each account has one encrypted workspace, shared across your devices through Sync.
 </p>
 </div>
 </div>
 </div>
 )}

 {error && (
 <div className="rounded-xl border border-danger/20 bg-danger/10 px-4 py-3 text-sm text-danger">
 {error}
 </div>
 )}

 <button
 onClick={
 step === 'username'
 ? handleUsername
 : step === 'pin' || step === 'pin_confirm'
 ? handlePin
 : handlePassphrase
 }
 disabled={loading || !inputValue.trim()}
 className="w-full rounded-xl bg-accent px-4 py-3 text-sm font-semibold text-surface transition-all hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-40"
 >
 {loading ? 'Working...' : buttonLabel}
 </button>

 <div className="flex items-center justify-between text-xs text-accent-muted">
 <button
 type="button"
 onClick={switchToLogin}
 className="hover:text-text-primary transition-colors"
 >
 {isRegister ? 'Back to login' : 'Use another account'}
 </button>
 {isRegister ? (
 <span></span>
 ) : (
 <button
 type="button"
 onClick={switchToRegister}
 className="hover:text-text-primary transition-colors"
 >
 Create account
 </button>
 )}
 </div>
 </div>
 </div>
 </div>
 )
}
