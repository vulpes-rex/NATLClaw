import { useState, useMemo } from 'react'
import { Todo, TodoFilter } from './types'
import TodoInput from './components/TodoInput'
import TodoList from './components/TodoList'
import TodoCounter from './components/TodoCounter'
import './App.css'

function App() {
  const [todos, setTodos] = useState<Todo[]>([])
  const [filter, setFilter] = useState<TodoFilter>('all')

  const addTodo = (text: string) => {
    const newTodo: Todo = {
      id: crypto.randomUUID(),
      text: text.trim(),
      completed: false,
      createdAt: new Date()
    }
    setTodos(prev => [newTodo, ...prev])
  }

  const toggleTodo = (id: string) => {
    setTodos(prev => prev.map(todo => 
      todo.id === id ? { ...todo, completed: !todo.completed } : todo
    ))
  }

  const deleteTodo = (id: string) => {
    setTodos(prev => prev.filter(todo => todo.id !== id))
  }

  const clearCompleted = () => {
    setTodos(prev => prev.filter(todo => !todo.completed))
  }

  const filteredTodos = useMemo(() => {
    switch (filter) {
      case 'active':
        return todos.filter(todo => !todo.completed)
      case 'completed':
        return todos.filter(todo => todo.completed)
      default:
        return todos
    }
  }, [todos, filter])

  const activeTodoCount = todos.filter(todo => !todo.completed).length
  const completedTodoCount = todos.filter(todo => todo.completed).length

  return (
    <div className="app">
      <header className="app-header">
        <h1 className="app-title">Todo List</h1>
        <TodoCounter 
          activeCount={activeTodoCount}
          completedCount={completedTodoCount}
        />
      </header>

      <TodoInput onAddTodo={addTodo} />

      <div className="filters">
        <button 
          className={`filter-btn ${filter === 'all' ? 'active' : ''}`}
          onClick={() => setFilter('all')}
        >
          All ({todos.length})
        </button>
        <button 
          className={`filter-btn ${filter === 'active' ? 'active' : ''}`}
          onClick={() => setFilter('active')}
        >
          Active ({activeTodoCount})
        </button>
        <button 
          className={`filter-btn ${filter === 'completed' ? 'active' : ''}`}
          onClick={() => setFilter('completed')}
        >
          Completed ({completedTodoCount})
        </button>
      </div>

      {filteredTodos.length === 0 ? (
        <div className="empty-state">
          {filter === 'all' 
            ? "No todos yet. Add one above!" 
            : filter === 'active' 
            ? "No active todos"
            : "No completed todos"
          }
        </div>
      ) : (
        <TodoList 
          todos={filteredTodos}
          onToggleTodo={toggleTodo}
          onDeleteTodo={deleteTodo}
        />
      )}

      {completedTodoCount > 0 && (
        <div style={{ textAlign: 'center', marginTop: '1rem' }}>
          <button onClick={clearCompleted} style={{ background: '#ef4444', color: 'white' }}>
            Clear Completed ({completedTodoCount})
          </button>
        </div>
      )}
    </div>
  )
}

export default App