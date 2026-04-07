import { useState, FormEvent } from 'react'

interface TodoInputProps {
  onAddTodo: (text: string) => void
}

const TodoInput = ({ onAddTodo }: TodoInputProps) => {
  const [inputValue, setInputValue] = useState('')

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault()
    const trimmed = inputValue.trim()
    if (trimmed) {
      onAddTodo(trimmed)
      setInputValue('')
    }
  }

  return (
    <form onSubmit={handleSubmit} style={{ marginBottom: '1.5rem' }}>
      <div style={{ display: 'flex', gap: '0.5rem' }}>
        <input
          type="text"
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          placeholder="What needs to be done?"
          style={{ flex: 1 }}
        />
        <button 
          type="submit"
          disabled={!inputValue.trim()}
          style={{ 
            background: inputValue.trim() ? 'var(--primary)' : '#ccc',
            color: 'white',
            cursor: inputValue.trim() ? 'pointer' : 'not-allowed'
          }}
        >
          Add Todo
        </button>
      </div>
    </form>
  )
}

export default TodoInput