# M12-01：release 当前 isMinifyEnabled=false，不混淆。
# 若后续开启 R8，需补充 Retrofit / kotlinx.serialization 的 keep 规则，
# 并把 token 相关类排除在日志/混淆映射之外（安全边界见 docs/DEVELOPMENT.md）。
