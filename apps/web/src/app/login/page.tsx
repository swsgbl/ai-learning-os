import { LoginForm } from "@/components/login/login-form";

// M11-01：页面保持路由职责，表单与状态反馈动效在 client leaf（LoginForm）。
export default function LoginPage() {
  return <LoginForm />;
}
