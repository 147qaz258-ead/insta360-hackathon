cmake_minimum_required(VERSION 3.20)

project(link2_web_controller LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(NOT WIN32)
    message(FATAL_ERROR "This sample uses the Windows build of Insta360 Link SDK.")
endif()

set(UVC_SDK_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/third_party/uvc/x64")

add_executable(link2_web_controller
    src/main.cpp
)

target_include_directories(link2_web_controller PRIVATE
    "${UVC_SDK_ROOT}/include"
)

target_link_libraries(link2_web_controller PRIVATE
    "${UVC_SDK_ROOT}/lib/UVCCamera.lib"
    ws2_32
)

target_compile_definitions(link2_web_controller PRIVATE
    WIN32_LEAN_AND_MEAN
    NOMINMAX
    _WIN32_WINNT=0x0A00
)

if(MSVC)
    target_compile_options(link2_web_controller PRIVATE /utf-8 /W4)
endif()

add_custom_command(TARGET link2_web_controller POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "${UVC_SDK_ROOT}/bin/UVCCamera.dll"
        "$<TARGET_FILE_DIR:link2_web_controller>/UVCCamera.dll"
    COMMAND ${CMAKE_COMMAND} -E copy_directory
        "${CMAKE_CURRENT_SOURCE_DIR}/web"
        "$<TARGET_FILE_DIR:link2_web_controller>/web"
)

