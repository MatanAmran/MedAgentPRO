import { Chat } from "./components/Chat";

// The chat screen (task 7, Figure 15). Everything runs in the browser against the chat server on
// 127.0.0.1:8000: there is nothing to render on the server, and no Next proxy in the way of the
// stream (S1).
export default function Home() {
  return <Chat />;
}
