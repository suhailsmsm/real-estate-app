import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { ApiError } from "./core/client";
import "./ui/theme.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Historical transaction data, not a live feed — refetching on every
      // window focus would burn requests to redraw identical numbers.
      refetchOnWindowFocus: false,
      staleTime: 5 * 60 * 1000,
      retry: (failureCount, error) => {
        // A 4xx is the caller's fault and fails identically on retry; only
        // network and server errors are worth a second attempt.
        if (error instanceof ApiError && error.status < 500) return false;
        return failureCount < 2;
      },
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
