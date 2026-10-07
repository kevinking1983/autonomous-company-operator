import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { Layout } from './components/Layout'
import { CompanyPack } from './pages/CompanyPack'
import { Inbox } from './pages/Inbox'
import { Memory } from './pages/Memory'
import { Overview } from './pages/Overview'
import { Reliability } from './pages/Reliability'
import { RunView } from './pages/RunView'
import { Runs } from './pages/Runs'
import { Tasks } from './pages/Tasks'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Overview />} />
          <Route path="tasks" element={<Tasks />} />
          <Route path="runs" element={<Runs />} />
          <Route path="runs/:runId" element={<RunView />} />
          <Route path="inbox" element={<Inbox />} />
          <Route path="reliability" element={<Reliability />} />
          <Route path="memory" element={<Memory />} />
          <Route path="company" element={<CompanyPack />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
