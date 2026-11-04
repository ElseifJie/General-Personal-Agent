import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import { registerServiceWorker } from "./pwa";
import "./styles.css";
import "./interaction/interaction.css";
import "./interaction/workbench.css";

const root = document.querySelector("#root");
if (!root) throw new Error("Missing application root");

if (import.meta.env.PROD) registerServiceWorker();

createRoot(root).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
);
