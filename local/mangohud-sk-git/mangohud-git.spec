%global appname MangoHud
%global commit d7654ebcefb3bdb106d581c937417aa2007eaeeb
%global shortcommit %(c=%{commit}; echo ${c:0:7})
%global git_date 20260108

%global imgui_ver 1.91.6
%global implot_ver 0.16

Name:           mangohud
Version:        0.8.3
Release:        1.%{git_date}git%{shortcommit}%{?dist}
Summary:        Vulkan and OpenGL overlay for monitoring FPS, temperatures, CPU/GPU load

License:        MIT
URL:            https://github.com/flightlessmango/MangoHud
Source0:        %{url}/archive/%{commit}/%{appname}-%{commit}.tar.gz

# Custom patches
Patch0:         0001-feat-display-battery-charging-power.patch
Patch1:         0002-Add-support-for-more-handheld-devices-to-fan-detecti.patch
Patch2:         0003-amdgpu-add-cpu-temperature-fallback-for-gpu_metrics-.patch

ExclusiveArch:  x86_64

BuildRequires:  appstream
BuildRequires:  dbus-devel
BuildRequires:  gcc-c++
BuildRequires:  git-core
BuildRequires:  glfw-devel
BuildRequires:  glslang-devel
BuildRequires:  libappstream-glib
BuildRequires:  libstdc++-static
BuildRequires:  mesa-libGL-devel
BuildRequires:  meson >= 0.60
BuildRequires:  python3-mako
BuildRequires:  spdlog-devel
BuildRequires:  vulkan-headers

BuildRequires:  pkgconfig(nlohmann_json)
BuildRequires:  pkgconfig(wayland-client)
BuildRequires:  pkgconfig(x11)
BuildRequires:  pkgconfig(xkbcommon)

Requires:       hicolor-icon-theme
Requires:       vulkan-loader%{?_isa}

Suggests:       %{name}-mangoplot
Suggests:       goverlay

Provides:       bundled(imgui) = %{imgui_ver}
Provides:       bundled(implot) = %{implot_ver}

%description
A Vulkan and OpenGL overlay for monitoring FPS, temperatures, CPU/GPU load and
more. Also includes MangoApp for standalone overlay display.


%package        mangoplot
Summary:        Local visualization "mangoplot" for %{name}
BuildArch:      noarch

Requires:       %{name} = %{version}-%{release}
Requires:       python3-matplotlib
Requires:       python3-numpy

%description    mangoplot
Local visualization "mangoplot" for %{name}.


%prep
%autosetup -n %{appname}-%{commit} -p1


%build
%meson \
    -Dmangoapp=true \
    -Dmangohudctl=true \
    -Dinclude_doc=true \
    -Duse_system_spdlog=enabled \
    -Dwith_wayland=enabled \
    -Dwith_xnvctrl=disabled \
    -Dtests=disabled \
    --wrap-mode=default \
    %{nil}
%meson_build


%install
%meson_install

# Fix ambiguous python shebang
sed -i "s@#!/usr/bin/env python@#!/usr/bin/python3@" \
    %{buildroot}%{_bindir}/mangoplot

# Remove unneeded static library
rm -f %{buildroot}%{_libdir}/libimgui.a


%check
appstream-util validate-relax --nonet %{buildroot}%{_metainfodir}/*.xml || :


%files
%license LICENSE
%doc README.md
%{_bindir}/mangoapp
%{_bindir}/mangohud
%{_bindir}/mangohudctl
%{_datadir}/icons/hicolor/scalable/*/*.svg
%{_datadir}/vulkan/implicit_layer.d/*Mango*.json
%{_docdir}/%{name}/%{appname}.conf.example
%{_docdir}/%{name}/presets.conf.example
%{_libdir}/%{name}/
%{_mandir}/man1/%{name}.1*
%{_mandir}/man1/mangoapp.1*
%{_metainfodir}/*.metainfo.xml

%files mangoplot
%{_bindir}/mangoplot


%changelog
* Thu Jan 09 2025 Package Maintainer <maintainer@example.com> - 0.8.3-1.%{git_date}git%{shortcommit}
- Build from git commit %{shortcommit}
- Custom patches for battery power display, handheld fan detection, APU CPU temp fallback
