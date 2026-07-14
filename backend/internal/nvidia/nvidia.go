package nvidia

import (
	"os/exec"
	"strings"
)

// Probe probes an NVIDIA GPU via nvidia-smi.
func Probe() (name string, usagePct float64, memUsed, memTotal uint64, ok bool) {
	out, err := exec.Command("nvidia-smi",
		"--query-gpu=name,utilization.gpu,memory.used,memory.total",
		"--format=csv,noheader,nounits",
	).Output()
	if err != nil {
		return "", 0, 0, 0, false
	}
	line := strings.SplitN(strings.TrimSpace(string(out)), "\n", 2)[0]
	parts := strings.Split(line, ",")
	if len(parts) < 4 {
		return "", 0, 0, 0, false
	}
	name = strings.TrimSpace(parts[0])
	usagePct = atof(strings.TrimSpace(parts[1]))
	memUsed = uint64(atof(strings.TrimSpace(parts[2]))) * 1024 * 1024
	memTotal = uint64(atof(strings.TrimSpace(parts[3]))) * 1024 * 1024
	return name, usagePct, memUsed, memTotal, true
}

func atof(s string) float64 {
	var f float64
	for _, r := range s {
		if r >= '0' && r <= '9' {
			f = f*10 + float64(r-'0')
		} else if r == '.' {
			break
		}
	}
	return f
}
