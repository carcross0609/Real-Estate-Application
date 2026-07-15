import * as React from "react";
import { cn } from "@/lib/utils";

const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, type, ...props }, ref) => (
    <input
      ref={ref}
      type={type}
      className={cn(
        "h-8 w-full rounded-md border border-stroke bg-panel px-2.5 text-[13px] text-ink",
        "placeholder:text-ink-3 transition-colors duration-150",
        "hover:border-stroke-strong",
        "focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/25",
        "disabled:cursor-not-allowed disabled:opacity-50",
        type === "number" && "figure",
        className,
      )}
      {...props}
    />
  ),
);
Input.displayName = "Input";

/** Input with a mono prefix/suffix affordance ("$", "%", "/mo") for financial fields. */
const UnitInput = React.forwardRef<
  HTMLInputElement,
  React.InputHTMLAttributes<HTMLInputElement> & { prefix?: string; suffix?: string }
>(({ className, prefix, suffix, ...props }, ref) => (
  <div
    className={cn(
      "flex h-8 w-full items-center overflow-hidden rounded-md border border-stroke bg-panel",
      "transition-colors duration-150 hover:border-stroke-strong",
      "focus-within:border-accent focus-within:ring-2 focus-within:ring-accent/25",
      className,
    )}
  >
    {prefix ? <span className="figure pl-2.5 text-xs text-ink-3">{prefix}</span> : null}
    <input
      ref={ref}
      className="figure h-full w-full min-w-0 bg-transparent px-2 text-[13px] text-ink placeholder:text-ink-3 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
      {...props}
    />
    {suffix ? <span className="figure pr-2.5 text-xs text-ink-3">{suffix}</span> : null}
  </div>
));
UnitInput.displayName = "UnitInput";

export { Input, UnitInput };
