import * as React from "react"
import { Check, ChevronsUpDown } from "lucide-react"

import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"

export interface AutocompleteOption {
  value: string
  label: string
}

interface AutocompleteProps {
  options: AutocompleteOption[]
  value: string
  onValueChange: (value: string) => void
  placeholder?: string
  emptyText?: string
  searchPlaceholder?: string
  allowCustom?: boolean
}

export function Autocomplete({
  options,
  value,
  onValueChange,
  placeholder = "Select option...",
  emptyText = "No options found.",
  searchPlaceholder = "Search or type to create...",
  allowCustom = false,
}: AutocompleteProps) {
  const [open, setOpen] = React.useState(false)
  const [search, setSearch] = React.useState("")

  const selectedOption = options.find((option) => option.value === value)
  const displayValue = selectedOption?.label || value || placeholder

  // Filter options based on search
  const filteredOptions = React.useMemo(() => {
    if (!search) return options
    return options.filter((option) =>
      option.label.toLowerCase().includes(search.toLowerCase())
    )
  }, [options, search])

  const handleSelect = (selectedValue: string) => {
    onValueChange(selectedValue === value ? "" : selectedValue)
    setOpen(false)
    setSearch("")
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault()

      if (search) {
        // Check if search matches an existing option
        const exactMatch = options.find(
          opt => opt.value.toLowerCase() === search.toLowerCase()
        )

        if (exactMatch) {
          // Select the matching option
          handleSelect(exactMatch.value)
        } else if (allowCustom && filteredOptions.length === 0) {
          // Create new custom value
          onValueChange(search)
          setOpen(false)
          setSearch("")
        } else if (filteredOptions.length > 0) {
          // Select first filtered option
          handleSelect(filteredOptions[0].value)
        }
      }
    } else if (e.key === "Escape") {
      setOpen(false)
      setSearch("")
    }
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          aria-expanded={open}
          className="w-full justify-between"
        >
          <span className="truncate">{displayValue}</span>
          <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-full p-0" align="start" style={{ width: 'var(--radix-popover-trigger-width)' }}>
        <div className="flex flex-col">
          <div className="p-2 border-b">
            <Input
              placeholder={searchPlaceholder}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={handleKeyDown}
              className="h-9"
              autoFocus
            />
          </div>
          <div className="max-h-[300px] overflow-y-auto p-1">
            {filteredOptions.length === 0 ? (
              <div className="py-6 text-center text-sm">
                {allowCustom && search ? (
                  <div>
                    <p className="text-muted-foreground">Press Enter to create:</p>
                    <p className="font-medium mt-1">"{search}"</p>
                  </div>
                ) : (
                  <p className="text-muted-foreground">{emptyText}</p>
                )}
              </div>
            ) : (
              filteredOptions.map((option) => (
                <div
                  key={option.value}
                  className={cn(
                    "relative flex cursor-pointer select-none items-center rounded-sm px-2 py-1.5 text-sm outline-none hover:bg-accent hover:text-accent-foreground",
                    value === option.value && "bg-accent"
                  )}
                  onClick={() => handleSelect(option.value)}
                >
                  <Check
                    className={cn(
                      "mr-2 h-4 w-4",
                      value === option.value ? "opacity-100" : "opacity-0"
                    )}
                  />
                  {option.label}
                </div>
              ))
            )}
          </div>
        </div>
      </PopoverContent>
    </Popover>
  )
}
