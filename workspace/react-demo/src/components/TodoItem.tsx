import { Todo } from '../types'

interface TodoItemProps {
  todo: Todo
  onToggle: () => void
  onDelete: () => void
}

const TodoItem = ({ todo, onToggle, onDelete }: TodoItemProps) => {
  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: '0.75rem',
      padding: '0.75rem',
      border: '1px solid var(--border)',
      borderRadius: '6px',
      marginBottom: '0.5rem',
      background: todo.completed ? '#f9fafb' : 'white'
    }}>
      <input
        type="checkbox"
        checked={todo.completed}
        onChange={onToggle}
        style={{ 
          transform: 'scale(1.2)',
          cursor: 'pointer'
        }}
      />
      
      <span style={{
        flex: 1,
        textDecoration: todo.completed ? 'line-through' : 'none',
        color: todo.completed ? '#6b7280' : 'var(--text)',
        fontSize: '16px'
      }}>
        {todo.text}
      </span>
      
      <span style={{
        fontSize: '12px',
        color: '#9ca3af'
      }}>
        {todo.createdAt.toLocaleDateString()}
      </span>
      
      <button
        onClick={onDelete}
        style={{
          background: '#ef4444',
          color: 'white',
          padding: '4px 8px',
          fontSize: '12px',
          borderRadius: '4px'
        }}
      >
        Delete
      </button>
    </div>
  )
}

export default TodoItem