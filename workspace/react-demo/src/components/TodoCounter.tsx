interface TodoCounterProps {
  activeCount: number
  completedCount: number
}

const TodoCounter = ({ activeCount, completedCount }: TodoCounterProps) => {
  const total = activeCount + completedCount

  return (
    <div style={{
      display: 'flex',
      justifyContent: 'center',
      gap: '2rem',
      marginTop: '0.5rem',
      fontSize: '14px',
      color: '#6b7280'
    }}>
      <span>
        <strong>{total}</strong> total
      </span>
      <span>
        <strong>{activeCount}</strong> active
      </span>
      <span>
        <strong>{completedCount}</strong> completed
      </span>
    </div>
  )
}

export default TodoCounter